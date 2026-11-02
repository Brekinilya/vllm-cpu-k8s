import argparse
import time

import torch


def measure(weight: torch.Tensor, tokens: int, repeats: int) -> float:
    x = torch.randn(tokens, weight.shape[1], dtype=weight.dtype)
    for _ in range(3):
        torch.nn.functional.linear(x, weight)
    started = time.perf_counter()
    for _ in range(repeats):
        torch.nn.functional.linear(x, weight)
    return (time.perf_counter() - started) / repeats


def main() -> None:
    parser = argparse.ArgumentParser(description="Time one Qwen2.5-1.5B MLP projection in different dtypes.")
    parser.add_argument("--threads", type=int, default=9)
    parser.add_argument("--out-features", type=int, default=8960)
    parser.add_argument("--in-features", type=int, default=1536)
    args = parser.parse_args()

    torch.set_num_threads(args.threads)
    print(f"threads={torch.get_num_threads()} avx512_bf16={torch.cpu._is_avx512_bf16_supported()}")

    for dtype in (torch.bfloat16, torch.float16, torch.float32):
        weight = torch.randn(args.out_features, args.in_features, dtype=dtype)
        for tokens in (1, 8, 512):
            seconds = measure(weight, tokens, repeats=200 if tokens == 1 else 20)
            gflops = 2 * tokens * weight.numel() / seconds / 1e9
            print(f"{str(dtype):15s} tokens={tokens:4d} {seconds * 1e3:8.3f} ms {gflops:7.1f} GFLOP/s")


if __name__ == "__main__":
    main()
