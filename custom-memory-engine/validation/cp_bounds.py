"""Exact (Clopper-Pearson) bounds used in Section 10.6 (H4). Pure standard library.
Two-sided 95% interval: upper limit solves P(X <= k | n, p) = 0.025; for k = 0 this is 1 - 0.025**(1/n)."""
from math import comb, log, ceil

def binom_cdf(k, n, p):
    return sum(comb(n, i) * p**i * (1 - p)**(n - i) for i in range(k + 1))

def cp_upper(k, n, alpha=0.05):
    if k >= n:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if binom_cdf(k, n, mid) > alpha / 2:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2

if __name__ == "__main__":
    print("zero observed leaks: exact upper limit of the two-sided 95% interval")
    print("   n   closed form 1-0.025^(1/n)   bisection")
    for n in (10, 20, 30, 36, 50, 100, 200, 400):
        cf = 1 - 0.025 ** (1 / n)
        assert abs(cf - cp_upper(0, n)) < 1e-9
        print(f"{n:4d}   {cf:.4f}                     {cp_upper(0, n):.4f}")
    print("\nscenarios needed for a zero-leak result to give an upper limit <= u")
    for u in (0.20, 0.15, 0.10, 0.05):
        n = ceil(log(0.025) / log(1 - u))
        assert 1 - 0.025 ** (1 / n) <= u < 1 - 0.025 ** (1 / (n - 1))
        print(f"  u = {u:.2f}: n >= {n}")
    print("\nillustration of clustering: 12 deletion events per scenario, 0 leaks")
    for ns in (10, 20, 40):
        ne = 12 * ns
        print(f"  scenarios={ns:3d} events={ne:4d}  scenario-level bound={cp_upper(0, ns):.3f}  event-level bound (independence assumed)={cp_upper(0, ne):.3f}")
    print("\none leaking scenario observed: upper limit of the two-sided 95% interval")
    for n in (20, 40, 100):
        print(f"  1 of {n:3d} scenarios: {cp_upper(1, n):.3f}")
