"""
A/B Test Analysis - Checkout Redesign Experiment
------------------------------------------------
Simulates a two-arm conversion experiment (control vs new checkout),
then runs the full analyst workflow:
 - conversion rates + lift
 - two-proportion z-test and 95% confidence interval on the difference
 - revenue-per-user comparison (Welch's t-test)
 - statistical power / minimum detectable effect check
Outputs charts + a findings summary.
"""
import numpy as np
import pandas as pd
from scipy import stats
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update({"figure.dpi": 120, "font.size": 10, "axes.grid": True,
                     "grid.alpha": 0.25, "axes.spines.top": False, "axes.spines.right": False})
BLUE, GREEN = "#2b6cb0", "#2f855a"
rng = np.random.default_rng(21)

# ---------- Design ----------
# Sample size is a decision made BEFORE the experiment runs, not a number you
# report afterwards. Pick the smallest lift worth detecting, then solve for n.
p_control = 0.114          # true baseline conversion
p_treat = 0.129            # true treatment conversion (+1.5pp)

ALPHA, POWER = 0.05, 0.80
TARGET_MDE = 0.015         # smallest absolute lift the business cares about (1.5pp)
_za = stats.norm.ppf(1 - ALPHA / 2)
_zb = stats.norm.ppf(POWER)
n_required = int(np.ceil(2 * p_control * (1 - p_control) * ((_za + _zb) / TARGET_MDE) ** 2))
n_per_arm = 8_000          # rounded up from n_required to a clean allocation
print(f"Design: detecting {TARGET_MDE*100:.1f}pp at {POWER:.0%} power (alpha={ALPHA}) "
      f"needs {n_required:,} per arm; running {n_per_arm:,}")

conv_c = rng.random(n_per_arm) < p_control
conv_t = rng.random(n_per_arm) < p_treat
# revenue only for converters (avg order value differs slightly)
rev_c = np.where(conv_c, rng.gamma(4, 14, n_per_arm), 0.0)
rev_t = np.where(conv_t, rng.gamma(4, 15, n_per_arm), 0.0)

df = pd.DataFrame({
    "user_id": np.arange(2*n_per_arm),
    "group": ["control"]*n_per_arm + ["treatment"]*n_per_arm,
    "converted": np.concatenate([conv_c, conv_t]).astype(int),
    "revenue": np.concatenate([rev_c, rev_t]).round(2),
})
df.to_csv("data/experiment.csv", index=False)

# ---------- Sample ratio mismatch ----------
# First thing to check in any real experiment: did the split come out as designed?
# If the randomiser or the logging is broken, nothing downstream is worth reading.
n_c, n_t = len(conv_c), len(conv_t)
srm_chi2 = (n_c - n_t) ** 2 / (n_c + n_t)
srm_p = 1 - stats.chi2.cdf(srm_chi2, df=1)
print(f"SRM check: {n_c:,} vs {n_t:,} (chi2 p = {srm_p:.3f})"
      + ("  OK" if srm_p > 0.01 else "  WARNING: split is not 50/50, stop here"))

# ---------- Conversion analysis ----------
x_c, x_t = conv_c.sum(), conv_t.sum()
cr_c, cr_t = x_c/n_per_arm, x_t/n_per_arm
abs_lift = cr_t - cr_c
rel_lift = abs_lift / cr_c

# Two-proportion z-test (pooled)
p_pool = (x_c + x_t) / (2*n_per_arm)
se_pool = np.sqrt(p_pool*(1-p_pool)*(2/n_per_arm))
z = abs_lift / se_pool
p_value = 2*(1 - stats.norm.cdf(abs(z)))
# 95% CI on the difference (unpooled SE)
se_unpool = np.sqrt(cr_c*(1-cr_c)/n_per_arm + cr_t*(1-cr_t)/n_per_arm)
ci = (abs_lift - 1.96*se_unpool, abs_lift + 1.96*se_unpool)

# ---------- Revenue per user: GUARDRAIL metric ----------
# Conversion is the primary metric and the ship decision rests on it alone.
# Revenue per user is a guardrail: it is here to catch the case where conversion
# goes up because people buy cheaper things. Treating both as primary would mean
# two tests at alpha=0.05 and an inflated false positive rate.
#
# Revenue is zero for everyone who did not convert, so the distribution is a spike
# at zero plus a skewed tail. A t-test survives that at this n by the CLT, but a
# bootstrap interval makes no distributional assumption, so use that instead.
t_stat, t_p = stats.ttest_ind(rev_t, rev_c, equal_var=False)
rpu_c, rpu_t = rev_c.mean(), rev_t.mean()
_bs = np.array([rng.choice(rev_t, n_per_arm).mean() - rng.choice(rev_c, n_per_arm).mean()
                for _ in range(5000)])
rpu_ci = (float(np.percentile(_bs, 2.5)), float(np.percentile(_bs, 97.5)))

# ---------- Power / MDE ----------
from math import sqrt
alpha, power = 0.05, 0.80
z_a, z_b = stats.norm.ppf(1-alpha/2), stats.norm.ppf(power)
mde = (z_a + z_b) * sqrt(2*p_control*(1-p_control)/n_per_arm)

print(f"Control CR : {cr_c*100:.2f}%  ({x_c}/{n_per_arm})")
print(f"Treat   CR : {cr_t*100:.2f}%  ({x_t}/{n_per_arm})")
print(f"Absolute lift: {abs_lift*100:+.2f}pp | Relative lift: {rel_lift*100:+.1f}%")
print(f"z = {z:.2f} | p-value = {p_value:.4f} | 95% CI diff = [{ci[0]*100:+.2f}pp, {ci[1]*100:+.2f}pp]")
print(f"Revenue/user: control ${rpu_c:.2f} vs treatment ${rpu_t:.2f} | Welch p = {t_p:.4f}")
print(f"MDE at 80% power, n={n_per_arm}/arm: {mde*100:.2f}pp")

# ---------- Chart 1: conversion with CI ----------
fig, ax = plt.subplots(figsize=(6, 4.5))
se_c = np.sqrt(cr_c*(1-cr_c)/n_per_arm); se_t = np.sqrt(cr_t*(1-cr_t)/n_per_arm)
ax.bar(["Control","Treatment"], [cr_c*100, cr_t*100], color=[BLUE, GREEN],
       yerr=[1.96*se_c*100, 1.96*se_t*100], capsize=8)
for i, v in enumerate([cr_c, cr_t]):
    ax.text(i, v*100+0.15, f"{v*100:.2f}%", ha="center", fontsize=10, fontweight="bold")
ax.set_ylabel("Conversion rate (%)")
sig = "statistically significant" if p_value < 0.05 else "not significant"
ax.set_title(f"Checkout A/B Test - {rel_lift*100:+.1f}% lift (p={p_value:.3f}, {sig})")
fig.tight_layout(); fig.savefig("charts/01_conversion_comparison.png"); plt.close()

# ---------- Chart 2: bootstrap distribution of the difference ----------
boot = []
for _ in range(5000):
    bc = rng.choice(conv_c, n_per_arm).mean()
    bt = rng.choice(conv_t, n_per_arm).mean()
    boot.append((bt-bc)*100)
boot = np.array(boot)
fig, ax = plt.subplots(figsize=(7.5, 4.2))
ax.hist(boot, bins=40, color=BLUE, alpha=0.85)
ax.axvline(0, color="#c53030", ls="--", label="No effect")
ax.axvline(abs_lift*100, color=GREEN, lw=2, label=f"Observed {abs_lift*100:+.2f}pp")
ax.set_title("Bootstrap distribution of conversion-rate difference (5,000 resamples)")
ax.set_xlabel("Treatment - Control conversion (pp)"); ax.legend()
fig.tight_layout(); fig.savefig("charts/02_bootstrap_difference.png"); plt.close()

with open("charts/findings.txt", "w") as f:
    f.write(f"Sample: {n_per_arm:,} users per arm\n")
    f.write(f"Control conversion : {cr_c*100:.2f}%\n")
    f.write(f"Treatment conversion: {cr_t*100:.2f}%\n")
    f.write(f"Absolute lift: {abs_lift*100:+.2f}pp | Relative lift: {rel_lift*100:+.1f}%\n")
    f.write(f"Two-proportion z-test: z={z:.2f}, p={p_value:.4f}\n")
    f.write(f"95% CI on difference: [{ci[0]*100:+.2f}pp, {ci[1]*100:+.2f}pp]\n")
    f.write(f"Revenue/user: control ${rpu_c:.2f} vs treatment ${rpu_t:.2f} (Welch p={t_p:.4f})\n")
    f.write(f"MDE at 80% power (n={n_per_arm}/arm): {mde*100:.2f}pp\n")
    f.write(f"Decision: {'SHIP - significant positive lift' if p_value<0.05 and abs_lift>0 else 'DO NOT SHIP'}\n")

print("Done. Charts + findings in charts/")
