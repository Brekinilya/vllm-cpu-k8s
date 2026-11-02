# vLLM на CPU в Kubernetes

Инференс `Qwen/Qwen2.5-1.5B-Instruct` на vLLM 0.32.0 в Minikube, строго на CPU. Образ собирается rootless, все зависимости зафиксированы с хэшами, промпты и ответы в логи не попадают, веса скачиваются один раз в PVC, секреты и параметры инференса вынесены в `Secret` и `ConfigMap`. Сделаны все три бонуса: Kustomize, initContainer на отдельном лёгком образе и нагрузочное тестирование.

## Содержание

- [Структура репозитория](#структура-репозитория)
- [Быстрый старт](#быстрый-старт)
- [Как закрыты требования](#как-закрыты-требования)
- [Образ vLLM](#образ-vllm)
- [Логи без промптов и ответов](#логи-без-промптов-и-ответов)
- [Хранилище и кэширование модели](#хранилище-и-кэширование-модели)
- [Секреты](#секреты)
- [Конфигурация через ConfigMap](#конфигурация-через-configmap)
- [Безопасность пода](#безопасность-пода)
- [Нагрузочное тестирование](#нагрузочное-тестирование)
- [Ограничения и что сделать в продакшене](#ограничения-и-что-сделать-в-продакшене)

## Структура репозитория

```
.
├── docker/
│   ├── vllm/                  Dockerfile vLLM, requirements.in, requirements.txt (lock с хэшами)
│   └── downloader/            Dockerfile загрузчика модели для initContainer, lock
├── src/vllm_privacy/          фильтр логов, ASGI-middleware для access-лога, logging.json
├── scripts/
│   ├── download_model.py      скачивание модели с Hugging Face в volume (один раз)
│   ├── make-secrets.sh        генерация secrets.env для Kustomize
│   ├── smoke_test.sh          проверка развёрнутого сервиса (auth, ответ, логи)
│   └── lock.sh                пересборка lock-файлов
├── deploy/
│   ├── base/                  Namespace, ServiceAccount, PVC, Deployment, Service, конфиги
│   └── overlays/minikube/     ресурсы под Minikube и генерация Secret
├── bench/                     нагрузочный тест, Job для кластера, результаты
└── tests/                     unit-тесты приватности логов
```

## Быстрый старт

Нужны Docker, minikube и kubectl ≥ 1.27 (Kustomize встроен в kubectl). Проверено на Windows 11 + Docker Desktop (WSL2, Docker выделено 12 GB RAM), minikube 1.39, Kubernetes 1.37. Команды ниже — для bash (Linux, macOS, Git Bash).

**1. Кластер.** Модели нужно около 8 GiB памяти, поэтому minikube получает 10 GB:

```bash
minikube start --driver=docker --cpus=16 --memory=10000mb --disk-size=40g --addons=metrics-server
```

**2. Образы.** Собираются локально и загружаются в minikube. Внутри minikube работает containerd, поэтому `minikube docker-env` здесь не подходит:

```bash
docker build -f docker/vllm/Dockerfile -t vllm-cpu:0.32.0 .
docker build -f docker/downloader/Dockerfile -t model-downloader:1.0.0 .
minikube image load vllm-cpu:0.32.0
minikube image load model-downloader:1.0.0
```

**3. Секреты.** Скрипт генерирует случайный API-ключ и пишет `deploy/overlays/minikube/secrets.env`; этот файл в `.gitignore`. Для закрытых (gated) моделей перед запуском нужно выставить `HF_TOKEN`:

```bash
HF_TOKEN=hf_xxx ./scripts/make-secrets.sh
```

Для Qwen2.5 токен не нужен, достаточно `./scripts/make-secrets.sh`.

**4. Деплой:**

```bash
kubectl apply -k deploy/overlays/minikube
kubectl -n llm-inference rollout status deploy/vllm --timeout=20m
```

Первый запуск долгий: initContainer качает 2.9 GiB весов, затем vLLM компилирует граф модели через `torch.compile`. Это около 10–12 минут. Последующие рестарты берут веса и кэш компиляции с PVC.

**5. Проверка.** Smoke-тест сам поднимает `port-forward` и проверяет `/health`, отказ без токена и с неверным токеном, ответ модели и то, что промпт не попал в логи:

```bash
./scripts/smoke_test.sh
```

Запрос вручную:

```bash
kubectl -n llm-inference port-forward svc/vllm 8000:8000
```

```bash
API_KEY=$(kubectl -n llm-inference get secret vllm-secrets -o jsonpath='{.data.API_KEY}' | base64 -d)
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"model": "qwen2.5-1.5b-instruct", "messages": [{"role": "user", "content": "Привет! Кто ты?"}], "max_tokens": 64}'
```

**Тесты фильтра логов.** Локально нужен только Python, без vLLM:

```bash
python -m unittest discover -s tests -v
```

Тест с настоящим `RequestLogger` из vLLM запускается внутри образа:

```bash
docker run --rm --entrypoint python -v "$PWD/tests:/tests:ro" -v "$PWD/src:/src:ro" vllm-cpu:0.32.0 -m unittest discover -s /tests -v
```

## Как закрыты требования

| Требование | Как сделано | Где |
|---|---|---|
| k8s / Minikube | Minikube, все ресурсы ставятся через `kubectl apply -k` | `deploy/` |
| Instruct-модель, CPU | `Qwen2.5-1.5B-Instruct` в bf16, ревизия зафиксирована коммитом HF; CPU-сборка vLLM | `deploy/base/config/model.env` |
| vLLM ≥ 0.27.0 | 0.32.0, официальный CPU-wheel из GitHub Releases | `docker/vllm/requirements.in` |
| Rootless | `USER 10001`, в поде `runAsNonRoot`, `drop: ALL`, read-only root FS | `docker/*/Dockerfile`, `deployment.yaml` |
| Pinned dependencies | 152 пакета с точными версиями и sha256, установка `--require-hashes --no-deps --only-binary=:all:` | `docker/vllm/requirements.txt` |
| Логи без промптов | фильтр на handler'е + своя access-middleware, подключены штатными механизмами vLLM | `src/vllm_privacy/` |
| PVC-кэш модели | первый запуск качает в PVC, дальше `cache hit` без сети, основной контейнер в offline-режиме | `scripts/download_model.py`, `pvc.yaml` |
| Secret | `HF_TOKEN` (только для initContainer) и `API_KEY` (Bearer для клиентов) | `deploy/overlays/minikube` |
| ConfigMap | аргументы `vllm serve` в YAML-файле, переменные среды и модель в отдельных ConfigMap | `deploy/base/config/` |
| Бонус 1: Kustomize | base + overlay, генераторы ConfigMap/Secret, хэш в именах ConfigMap для автоматического rollout | `deploy/` |
| Бонус 2: initContainer | отдельный rootless-образ на `python:3.12-alpine`, 125 MB против 5 GB у vLLM | `docker/downloader/` |
| Бонус 3: нагрузка | Job в кластере, TTFT / latency / throughput, короткие и длинные промпты, эксперименты с параметрами | `bench/` |

## Образ vLLM

[`docker/vllm/Dockerfile`](docker/vllm/Dockerfile) — трёхстадийная сборка на `ubuntu:24.04`. Образ закреплён по digest. Ubuntu 24.04 выбрана потому, что CPU-wheel vLLM собран под `manylinux_2_39` и требует glibc 2.39.

- **builder:** создаёт venv и ставит пакеты строго по lock-файлу: `--require-hashes` (хэш каждого файла сверяется), `--no-deps` (pip не может доустановить что-то не из lock-файла), `--only-binary=:all:` (никакой сборки из исходников). После установки pip удаляется из venv.
- **runtime:** только Python, `libtcmalloc` и `libnuma`, которые нужны CPU-бэкенду vLLM, `g++` и заголовки Python. Компилятор нужен потому, что vLLM на CPU компилирует ядра модели через `torch.compile` (Inductor) прямо при старте; без него движок падает с `InvalidCxxCompiler`. В официальном CPU-образе vLLM компилятор тоже есть.
- Процесс работает от `uid 10001`. Сеть Hugging Face в образе выключена по умолчанию (`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`), сбор статистики vLLM отключён (`VLLM_NO_USAGE_STATS=1`).

**Воспроизводимость зависимостей.** Lock-файл генерирует [`scripts/lock.sh`](scripts/lock.sh) через `uv pip compile`:
- целевая платформа Linux x86_64 и Python 3.12 задаются явно, поэтому lock собирается на любой ОС;
- `--exclude-newer 2026-10-20` отсекает пакеты, вышедшие позже релиза vLLM 0.32.0;
- индекс PyTorch подключается только для пакетов torch (`--torch-backend cpu`).

Без последнего пункта индекс PyTorch, который зеркалит часть обычных пакетов без дат загрузки, давал разные версии транзитивных зависимостей от запуска к запуску. Сейчас два прогона `lock.sh` дают побайтово одинаковый файл.

**Почему 0.32.0, а не 0.33.0.** 0.33.0 вышла в день выполнения задания. 0.32.0 к этому моменту стабильна уже две недели, а требование «≥ 0.27.0» выполнено в обоих случаях.

## Логи без промптов и ответов

Код в [`src/vllm_privacy`](src/vllm_privacy), в образ попадает через `PYTHONPATH`. Форк vLLM не нужен: используются два штатных механизма — `VLLM_LOGGING_CONFIG_PATH` и аргумент `--middleware`.

Через логи vLLM содержимое запросов может утечь так:

| Источник | Что пишет | Что делаем |
|---|---|---|
| `RequestLogger.log_inputs` (INFO, при `--enable-log-requests`) | id запроса и `SamplingParams`; в параметрах бывают stop-строки, JSON-схемы и прочие данные пользователя | оставляем только id и `max_tokens` |
| `RequestLogger.log_inputs` (DEBUG) | сам промпт и token ids | запись отбрасывается |
| `RequestLogger.log_request_body` (DEBUG) | JSON тела запроса | запись отбрасывается |
| `RequestLogger.log_outputs` (при `--enable-log-outputs`) | текст ответа | заменяем на длину ответа и `finish_reason` |
| `VLLM_DEBUG_LOG_API_SERVER_RESPONSE` | тела ответов | логгер `log_response` глушится целиком |
| tool/reasoning-парсеры | куски вывода модели в предупреждениях | строковые аргументы заменяются на `<redacted>` |
| исключения в `vllm.entrypoints` и `uvicorn.error` | текст исключения может содержать фрагменты ввода | traceback сохраняется, сообщение исключения заменяется на `<redacted>` |

`PrivacyFilter` висит на handler'е, а не на логгере, поэтому через него проходят и записи дочерних логгеров. Для логгера `RequestLogger` фильтр работает по белому списку: незнакомое сообщение с аргументами отбрасывается. Если в будущей версии vLLM появится новый формат записи, он не утечёт, а просто пропадёт из лога.

Факт обращения пишет [`AccessLogMiddleware`](src/vllm_privacy/middleware.py). Это чистая ASGI-middleware: тело запроса она не читает, стриминг не ломает. Подключена последней, поэтому видит и ответы `401` от встроенной проверки токена. На каждый запрос пишется одна строка:

```
INFO 2026-11-02T14:34:28 [vllm_privacy.access:55] 127.0.0.1 POST /v1/chat/completions status=200 duration_ms=9213.9 response_bytes=716 request_id=3edff0dfa03849a5ae79dc9b34cb1321
INFO 2026-11-02T14:34:28 [vllm.entrypoints.serve.utils.request_logger:65] Received request chatcmpl-8f5064f82b1dcb2c: max_tokens=32
```

Строка содержит IP клиента, метод, путь без query string, статус, длительность (для стриминга — до конца потока), размер ответа и `X-Request-Id`; последний совпадает с id запроса в логах движка. Запросы проб `/health` и `/metrics` с успешным статусом не логируются, чтобы не засорять вывод. Стандартный access-лог uvicorn отключён (`disable-uvicorn-access-log`), так как его заменяет middleware.

Как проверено:

- 15 unit-тестов в [`tests/`](tests). Один из них прогоняет настоящий `RequestLogger` vLLM с уровнем DEBUG, включённым логированием ответов и строкой-маркером в промпте, stop-строках и ответе, и проверяет, что маркера в выводе нет.
- `scripts/smoke_test.sh` на живом кластере отправляет промпт с уникальным маркером, убеждается, что маркер есть в ответе модели, и затем ищет его в `kubectl logs`.

## Хранилище и кэширование модели

PVC `vllm-models` (10 Gi, RWO) хранит веса модели и кэш компиляции. В поде три контейнера:

1. **`prepare-cache`** (initContainer) создаёт каталог `.compile-cache` на PVC. Отдельный шаг нужен потому, что при монтировании через `subPath` kubelet создаёт отсутствующий каталог от root, а на hostPath-томах minikube `fsGroup` не применяется.
2. **`model-downloader`** (initContainer) запускает [`scripts/download_model.py`](scripts/download_model.py):
   - если в `MODEL_DIR` лежит маркер `.download-complete.json` с тем же `MODEL_ID` и `MODEL_REVISION`, сразу завершается (`cache hit ... network is not used`);
   - иначе качает модель во временный каталог `.<name>.partial` (после обрыва докачка продолжится с того же места), пишет маркер и атомарно переименовывает каталог.
   Сменить модель или ревизию в ConfigMap — значит скачать новую; повторный старт с теми же значениями сеть не трогает.
3. **`vllm`** монтирует каталог моделей **только на чтение** и работает с `HF_HUB_OFFLINE=1`. Даже при пустом кэше он не пойдёт в сеть, а упадёт.

Кэши `torch.compile`, Inductor и Triton направлены в `/home/vllm/.cache`, который смонтирован на PVC через `subPath`. Остальная файловая система контейнера read-only.

Что проверено:

```
$ kubectl -n llm-inference logs <pod> -c model-downloader   # первый запуск
[model-downloader] cache miss: downloading Qwen/Qwen2.5-1.5B-Instruct@989aa79... into /models/Qwen2.5-1.5B-Instruct
[model-downloader] downloaded 2.89 GiB in 265s
[model-downloader] model is ready

$ kubectl -n llm-inference delete pod -l app.kubernetes.io/name=vllm
$ kubectl -n llm-inference logs <new-pod> -c model-downloader
[model-downloader] cache hit: Qwen/Qwen2.5-1.5B-Instruct@989aa79... is already in /models/Qwen2.5-1.5B-Instruct, network is not used
```

Стратегия деплоя — `Recreate`, потому что RWO-том нельзя одновременно смонтировать в старый и новый под на разных нодах. Кроме того, так в один момент времени в кэш пишет только один загрузчик.

## Секреты

Secret `vllm-secrets` генерируется Kustomize из файла `deploy/overlays/minikube/secrets.env`; сам файл в git не попадает, в репозитории лежит только `secrets.env.example`.

- `HF_TOKEN` — опциональный (`optional: true`). Передаётся **только** в initContainer: основному контейнеру токен Hugging Face не нужен.
- `API_KEY` — передаётся в vLLM как `VLLM_API_KEY`. Все пути `/v1/*` без заголовка `Authorization: Bearer <key>` получают `401`; `/health` открыт для проб.

У Secret отключён хэш-суффикс имени: Job нагрузочного теста ссылается на него по постоянному имени. После смены ключа нужен `kubectl -n llm-inference rollout restart deploy/vllm`.

## Конфигурация через ConfigMap

Все параметры лежат в [`deploy/base/config/`](deploy/base/config) и через `configMapGenerator` превращаются в три ConfigMap:

| ConfigMap | Файл | Что внутри |
|---|---|---|
| `vllm-serve` | `serve.yaml` | аргументы `vllm serve --config`: длина контекста, размер батча, размер KV-кэша, prefix caching, логирование, middleware |
| `vllm-runtime` | `runtime.env` | переменные среды CPU-бэкенда: привязка OpenMP-потоков, резерв ядер, AOT-компиляция |
| `vllm-model` | `model.env` | `MODEL_ID`, `MODEL_REVISION`, `MODEL_DIR`; общие для загрузчика и vLLM |

Путь к модели передаётся в vLLM как `$(MODEL_DIR)` в `args`, поэтому модель задаётся в одном месте и для загрузчика, и для сервера.

Kustomize дописывает к имени ConfigMap хэш содержимого. Поэтому любое изменение конфига меняет шаблон пода, и Deployment сам перекатывается — без пересборки образа и без ручного `rollout restart`:

```bash
sed -i 's/^max-num-seqs: .*/max-num-seqs: 32/' deploy/base/config/serve.yaml
kubectl apply -k deploy/overlays/minikube
```

Так в ходе работы менялись размер KV-кэша и переменная `VLLM_USE_AOT_COMPILE`, и так же запускались эксперименты из раздела про нагрузку.

## Безопасность пода

- Namespace помечен `pod-security.kubernetes.io/enforce: restricted`: под без этих настроек API-сервер просто не создаст.
- Все контейнеры: `runAsNonRoot`, `uid/gid 10001`, `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault`, `readOnlyRootFilesystem: true`.
- Запись разрешена только в `emptyDir` для `/tmp` и `/dev/shm` и в каталог кэша на PVC; каталог с весами в основном контейнере смонтирован только на чтение.
- `automountServiceAccountToken: false`: подам не нужен доступ к Kubernetes API. `enableServiceLinks: false`: в окружение не попадают переменные о чужих сервисах.
- `requests == limits` по CPU и памяти, класс QoS `Guaranteed`.
- Пробы: `startupProbe` даёт до 15 минут на загрузку весов и компиляцию, после этого работают `readiness` и `liveness`.

<!-- BENCH -->

## Ограничения и что сделать в продакшене

- **Секреты в продакшене** — через External Secrets / Sealed Secrets / Vault вместо файла рядом с манифестами.
- **Образы** — собирать в CI и пушить в registry с подписью (cosign) и SBOM, ссылаться по digest. Сейчас образы загружаются в minikube локально.
- **Сеть** — NetworkPolicy: доступ к 8000 только от ingress/шлюза; egress основному контейнеру не нужен вообще. В minikube с CNI по умолчанию NetworkPolicy не применяются, поэтому сюда не добавлены.
- **Компилятор в рантайм-образе** — вынужденная мера для `torch.compile` на CPU. Альтернатива — `enforce-eager: true` и образ без `g++`, ценой производительности.
- **AOT-кэш компиляции vLLM 0.32** на CPU не загружается (`'function' object has no attribute 'finalize_loading'`), поэтому выключен (`VLLM_USE_AOT_COMPILE=0`), а кэш Inductor/Triton лежит на PVC.
- **Масштабирование** — при RWO-томе одна реплика. Для нескольких реплик нужен RWX-том или общий кэш (S3 / NFS / OCI-артефакт) с загрузкой на каждую ноду, а также HPA по метрикам vLLM (`/metrics`: очередь, KV-кэш).
