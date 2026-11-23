#!/usr/bin/env python3
"""
01_code_library/40_web_browser_and_storefront/console_utility_hub/build_specs.py
--------------------------------------------------------------------------------
Deterministic Spec Compiler & Static Asset Builder for Track T1 (Milestone M1):
  - Tool 1 (/tools/gta-6-storage-calculator): Console 212 GB Storage & Download Calculator
  - Tool 2 (/tools/af1-resale-fee-calculator): Travis Scott x Nike AF1 Resale Fee & Profit Comparator

Governing Invariants (k):
  1. Zero Rockstar/Take-Two strings in root domain config, brand names, SKUs, or logos.
  2. Game title appears ONLY in post-domain path (/tools/gta-6-storage-calculator) with an
     unavoidable above-the-fold "Independent, unofficial utility not affiliated with Rockstar Games
     or Take-Two Interactive" banner.
  3. Zero Amazon live price scraping, price tracking, or price alerts (April 14, 2026 Policy).
     Static editorial hardware spec tiers only + adjacent FTC "Paid link / Affiliate disclosure"
     on every outbound button (16 CFR Part 255).
  4. Built for Cloudflare Pages Free + R2 ($0 commercial static edge; zero Vercel Hobby dependencies).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HUB_ROOT = Path(__file__).resolve().parent
DIST_DIR = HUB_ROOT / "dist"

BRAND_CONFIG: dict[str, Any] = {
    "brand_name": "RigSpec Utility Lab",
    "domain_placeholder": "consolebench.io",
    "hosting_target": "Cloudflare Pages Free + R2 (Commercial Permitted)",
    "ftc_disclosure_banner": (
        "FTC 16 CFR Part 255 Affiliate Disclosure: This page contains outbound affiliate links "
        "(each marked \"Paid link\"). If you purchase hardware or authenticated footwear through "
        "these links, we may earn a commission from Amazon Associates, B&H Photo, Adorama, eBay "
        "Partner Network, or StockX at zero additional cost to you. No live prices are scraped or tracked."
    ),
    "unofficial_disclaimer_banner": (
        "Independent, unofficial utility not affiliated with, endorsed by, or sponsored by "
        "Rockstar Games, Take-Two Interactive, Sony Interactive Entertainment, Microsoft, or Nike."
    ),
}

STORAGE_SPEC_DATA: dict[str, Any] = {
    "schema": "411.m1.storage_calculator/1",
    "base_install_gb": 212.0,
    "day_one_patch_and_cache_buffer_gb": 88.0,
    "recommended_total_footprint_gb": 300.0,
    "consoles": [
        {
            "id": "ps5_launch",
            "name": "PlayStation 5 (Launch Standard / Digital - 825 GB Raw)",
            "raw_gb": 825.0,
            "usable_gb": 667.2,
            "expansion_interface": "PCIe Gen4 x4 M.2 NVMe with Heatsink (>= 5,500 MB/s read)",
            "footprint_pct_of_usable": round((212.0 / 667.2) * 100.0, 1),
            "buffer_pct_of_usable": round((300.0 / 667.2) * 100.0, 1),
            "compatible_tiers": ["nvme_wd_sn850x_2tb", "nvme_samsung_990pro_2tb"],
        },
        {
            "id": "ps5_slim",
            "name": "PlayStation 5 Slim (1 TB Raw)",
            "raw_gb": 1000.0,
            "usable_gb": 842.0,
            "expansion_interface": "PCIe Gen4 x4 M.2 NVMe with Heatsink (>= 5,500 MB/s read)",
            "footprint_pct_of_usable": round((212.0 / 842.0) * 100.0, 1),
            "buffer_pct_of_usable": round((300.0 / 842.0) * 100.0, 1),
            "compatible_tiers": ["nvme_wd_sn850x_2tb", "nvme_samsung_990pro_2tb"],
        },
        {
            "id": "xbox_series_x",
            "name": "Xbox Series X (1 TB Raw)",
            "raw_gb": 1000.0,
            "usable_gb": 802.0,
            "expansion_interface": "CFexpress Type-B Proprietary Storage Expansion Card (Velocity Architecture)",
            "footprint_pct_of_usable": round((212.0 / 802.0) * 100.0, 1),
            "buffer_pct_of_usable": round((300.0 / 802.0) * 100.0, 1),
            "compatible_tiers": ["xbox_wd_c50_1tb", "xbox_seagate_2tb"],
        },
        {
            "id": "xbox_series_s",
            "name": "Xbox Series S (512 GB Launch Standard)",
            "raw_gb": 512.0,
            "usable_gb": 364.0,
            "expansion_interface": "CFexpress Type-B Proprietary Storage Expansion Card (Velocity Architecture)",
            "footprint_pct_of_usable": round((212.0 / 364.0) * 100.0, 1),
            "buffer_pct_of_usable": round((300.0 / 364.0) * 100.0, 1),
            "compatible_tiers": ["xbox_wd_c50_1tb", "xbox_seagate_2tb"],
        },
    ],
    "isp_speeds_mbps": [
        {"mbps": 10, "label": "10 Mbps (Basic Broadband / Hotspot)"},
        {"mbps": 25, "label": "25 Mbps (Standard 4G / LTE Fixed)"},
        {"mbps": 50, "label": "50 Mbps (Entry Fiber / 5G CPE)"},
        {"mbps": 100, "label": "100 Mbps (Fast Fiber / Starlink Avg)"},
        {"mbps": 250, "label": "250 Mbps (High-Speed Fiber)"},
        {"mbps": 500, "label": "500 Mbps (Gigabit-Class Fiber)"},
    ],
    "editorial_hardware_tiers": {
        "nvme_wd_sn850x_2tb": {
            "sku_code": "WDS200T2XHE",
            "title": "WD_BLACK 2TB SN850X NVMe Internal Gaming SSD with Heatsink",
            "interface": "PCIe Gen4 x4 M.2 2280",
            "seq_read_mbs": 7300,
            "form_factor_check": "PS5 M.2 Bay Verified (Integrated Low-Profile Heatsink)",
            "editorial_tier": "2 TB Performance Tier (Holds Base 212 GB + 300 GB Buffer + 12-15 AAA Titles)",
            "retailers": [
                {
                    "merchant": "Amazon (PC Components)",
                    "disclosure": "Paid link",
                    "url": "https://www.amazon.com/s?k=WD_BLACK+2TB+SN850X+NVMe+Heatsink+WDS200T2XHE&tag=rigspec411-20",
                },
                {
                    "merchant": "B&H Photo Video",
                    "disclosure": "Paid link",
                    "url": "https://www.bhphotovideo.com/c/search?Ntt=WD+BLACK+SN850X+2TB+Heatsink",
                },
                {
                    "merchant": "Adorama",
                    "disclosure": "Paid link",
                    "url": "https://www.adorama.com/l/?searchinfo=WD+BLACK+SN850X+2TB+Heatsink",
                },
            ],
        },
        "nvme_samsung_990pro_2tb": {
            "sku_code": "MZ-V9P2T0CW",
            "title": "Samsung 990 PRO 2TB PCIe Gen4 M.2 NVMe SSD with Heatsink",
            "interface": "PCIe Gen4 x4 M.2 2280",
            "seq_read_mbs": 7450,
            "form_factor_check": "PS5 M.2 Bay Verified (8.2mm Heatsink Clearance)",
            "editorial_tier": "2 TB Flagship Low-Latency Tier (Max Random IOPS for Asset Streaming)",
            "retailers": [
                {
                    "merchant": "Amazon (PC Components)",
                    "disclosure": "Paid link",
                    "url": "https://www.amazon.com/s?k=Samsung+990+PRO+2TB+Heatsink+MZ-V9P2T0CW&tag=rigspec411-20",
                },
                {
                    "merchant": "B&H Photo Video",
                    "disclosure": "Paid link",
                    "url": "https://www.bhphotovideo.com/c/search?Ntt=Samsung+990+PRO+2TB+Heatsink",
                },
                {
                    "merchant": "Adorama",
                    "disclosure": "Paid link",
                    "url": "https://www.adorama.com/l/?searchinfo=Samsung+990+PRO+2TB+Heatsink",
                },
            ],
        },
        "xbox_wd_c50_1tb": {
            "sku_code": "WDBMPH0010BNC-WCSN",
            "title": "WD_BLACK 1TB C50 Expansion Card for Xbox Series X|S",
            "interface": "Xbox Velocity Architecture Direct Slot",
            "seq_read_mbs": 2400,
            "form_factor_check": "Plug-and-Play Rear Expansion Slot (Supports Quick Resume)",
            "editorial_tier": "1 TB Essential Expansion Tier (Eliminates Series S 58.2% Single-Game Lockout)",
            "retailers": [
                {
                    "merchant": "Amazon (Gaming Storage)",
                    "disclosure": "Paid link",
                    "url": "https://www.amazon.com/s?k=WD_BLACK+1TB+C50+Expansion+Card+Xbox&tag=rigspec411-20",
                },
                {
                    "merchant": "B&H Photo Video",
                    "disclosure": "Paid link",
                    "url": "https://www.bhphotovideo.com/c/search?Ntt=WD+BLACK+C50+1TB+Xbox",
                },
                {
                    "merchant": "Adorama",
                    "disclosure": "Paid link",
                    "url": "https://www.adorama.com/l/?searchinfo=WD+BLACK+C50+1TB+Xbox",
                },
            ],
        },
        "xbox_seagate_2tb": {
            "sku_code": "STJR2000400",
            "title": "Seagate 2TB Storage Expansion Card for Xbox Series X|S",
            "interface": "Xbox Velocity Architecture Direct Slot",
            "seq_read_mbs": 2400,
            "form_factor_check": "Official Licensed Solid-State Expansion (Runs Series X|S Native Games)",
            "editorial_tier": "2 TB Marathon Expansion Tier (Full Library + Zero External USB Transfer Wait)",
            "retailers": [
                {
                    "merchant": "Amazon (Gaming Storage)",
                    "disclosure": "Paid link",
                    "url": "https://www.amazon.com/s?k=Seagate+2TB+Storage+Expansion+Card+Xbox+STJR2000400&tag=rigspec411-20",
                },
                {
                    "merchant": "B&H Photo Video",
                    "disclosure": "Paid link",
                    "url": "https://www.bhphotovideo.com/c/search?Ntt=Seagate+2TB+Xbox+Expansion+Card",
                },
                {
                    "merchant": "Adorama",
                    "disclosure": "Paid link",
                    "url": "https://www.adorama.com/l/?searchinfo=Seagate+2TB+Xbox+Expansion+Card",
                },
            ],
        },
    },
}

AF1_RESALE_SPEC_DATA: dict[str, Any] = {
    "schema": "411.m1.af1_resale_comparator/1",
    "drop_window": "Nov 21, 2026 SNKRS Release & Nov 23, 2026 CactusJack.com Capsule",
    "default_retail_usd": 130.0,
    "default_resale_ask_usd": 300.0,
    "default_shipping_usd": 14.0,
    "sku_catalog": [
        {
            "sku_code": "JJ7602-100",
            "name": "Cactus Jack x Air Force 1 Low 'Utopia White / Miami Cyan Accent'",
            "retail_usd": 130.0,
            "release_date": "2026-11-21",
            "edition_note": "General SNKRS Draw Release",
            "ebay_query": "Air+Force+1+JJ7602-100+Authenticity+Guarantee",
            "stockx_query": "air-force-1-low-cactus-jack-jj7602-100",
        },
        {
            "sku_code": "JJ7604-001",
            "name": "Cactus Jack x Air Force 1 Low 'Obsidian Night / Neon Coral'",
            "retail_usd": 130.0,
            "release_date": "2026-11-21",
            "edition_note": "SNKRS & Tier-0 Boutique Release",
            "ebay_query": "Air+Force+1+JJ7604-001+Authenticity+Guarantee",
            "stockx_query": "air-force-1-low-cactus-jack-jj7604-001",
        },
        {
            "sku_code": "CJ-AF1-CAMO-600",
            "name": "Cactus Jack x Air Force 1 Low 'Tropical Gradient Camo' (600-Pair Numbered)",
            "retail_usd": 150.0,
            "release_date": "2026-11-23",
            "edition_note": "CactusJack.com Exclusive (600 Pairs Worldwide)",
            "ebay_query": "Travis+Scott+Air+Force+1+Gradient+Camo+Authenticity+Guarantee",
            "stockx_query": "travis-scott-air-force-1-gradient-camo",
        },
    ],
    "platforms": {
        "ebay": {
            "name": "eBay Sneakers (Authenticity Guarantee)",
            "fee_threshold_usd": 150.0,
            "fee_rate_above_threshold": 0.08,
            "fee_rate_below_threshold": 0.1325,
            "fixed_order_fee_usd": 0.40,
            "auth_fee_buyer_usd": 0.0,
            "buyer_processing_rate": 0.0,
            "affiliate_program": "eBay Partner Network (4% Fashion Commission)",
            "disclosure": "Paid link",
        },
        "stockx": {
            "name": "StockX (Level 1 Seller Baseline)",
            "transaction_fee_rate": 0.09,
            "payment_processing_rate": 0.03,
            "combined_seller_rate": 0.12,
            "minimum_seller_shipping_usd": 5.0,
            "buyer_processing_rate": 0.049,
            "buyer_shipping_usd": 14.95,
            "affiliate_program": "StockX Affiliate Program (4% New / 2% Returning)",
            "disclosure": "Paid link",
        },
    },
}


def atomic_write(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_governing_invariants(file_map: dict[str, str]) -> None:
    """Enforce Invariant 1 (Rockstar Ring-Fence) & Invariant 4 (FTC disclosures)."""
    forbidden_in_brand = ("gta", "gta6", "gtavi", "grandtheftauto", "vicecity", "leonida", "rockstar")
    brand_blob = json.dumps(BRAND_CONFIG["brand_name"] + " " + BRAND_CONFIG["domain_placeholder"]).lower()
    for term in forbidden_in_brand:
        if term in brand_blob:
            raise ValueError(f"FAIL_FAST Invariant 1 Violation: Forbidden string '{term}' in brand/root domain config.")

    for rel_path, html in file_map.items():
        if rel_path.endswith(".html"):
            if "Independent, unofficial utility not affiliated with" not in html:
                raise ValueError(f"FAIL_FAST Invariant 1 Violation: Missing unofficial banner in {rel_path}")
            if "Paid link" not in html or "FTC 16 CFR Part 255" not in html:
                raise ValueError(f"FAIL_FAST Invariant 4 Violation: Missing FTC Paid link disclosure in {rel_path}")


SHARED_CSS = """\
:root {
  --bg: #0b0d12;
  --panel: #131722;
  --panel-alt: #1a2030;
  --border: #2a324b;
  --text: #eef2f9;
  --muted: #9aa6c1;
  --accent: #00e5be;
  --accent-warn: #ffb340;
  --accent-danger: #ff5470;
  --btn-bg: #00e5be;
  --btn-text: #061014;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.55;
  padding-bottom: 3rem;
}
.compliance-top-bar {
  background: #171c2b;
  border-bottom: 2px solid var(--accent-warn);
  padding: 0.75rem 1.25rem;
  font-size: 0.84rem;
}
.compliance-top-bar .unofficial-banner {
  color: #ffd166;
  font-weight: 700;
  margin-bottom: 0.25rem;
}
.compliance-top-bar .ftc-banner {
  color: var(--muted);
}
header.site-header {
  max-width: 1120px;
  margin: 0 auto;
  padding: 1.25rem 1.25rem 0.75rem;
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  border-bottom: 1px solid var(--border);
}
.brand-title {
  font-size: 1.2rem;
  font-weight: 800;
  letter-spacing: -0.02em;
  color: var(--accent);
  text-decoration: none;
}
nav.tool-nav {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
}
nav.tool-nav a {
  color: var(--text);
  text-decoration: none;
  background: var(--panel);
  border: 1px solid var(--border);
  padding: 0.45rem 0.85rem;
  border-radius: 6px;
  font-size: 0.88rem;
  font-weight: 600;
}
nav.tool-nav a:hover, nav.tool-nav a.active {
  border-color: var(--accent);
  color: var(--accent);
}
main.container {
  max-width: 1120px;
  margin: 1.5rem auto;
  padding: 0 1.25rem;
}
h1 {
  font-size: 1.85rem;
  margin-bottom: 0.5rem;
  letter-spacing: -0.02em;
}
.subtitle {
  color: var(--muted);
  margin-bottom: 1.5rem;
  font-size: 1rem;
}
.grid-2 {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));
  gap: 1.25rem;
}
.card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1.35rem;
}
.card h2 {
  font-size: 1.15rem;
  margin-bottom: 1rem;
  color: var(--accent);
}
label {
  display: block;
  font-size: 0.88rem;
  font-weight: 600;
  margin-bottom: 0.35rem;
  color: var(--muted);
}
select, input[type="number"] {
  width: 100%;
  padding: 0.65rem 0.75rem;
  margin-bottom: 1rem;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text);
  font-size: 0.95rem;
}
.metric-box {
  background: var(--panel-alt);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1rem;
  margin-bottom: 0.85rem;
}
.metric-label {
  font-size: 0.82rem;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.metric-value {
  font-size: 1.55rem;
  font-weight: 800;
  margin-top: 0.15rem;
}
.status-ok { color: var(--accent); }
.status-warn { color: var(--accent-warn); }
.status-danger { color: var(--accent-danger); }
.hardware-list {
  margin-top: 1.25rem;
  display: grid;
  gap: 1rem;
}
.hw-item {
  background: var(--panel-alt);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1.1rem;
}
.hw-title {
  font-weight: 700;
  font-size: 1.02rem;
  margin-bottom: 0.3rem;
}
.hw-meta {
  font-size: 0.85rem;
  color: var(--muted);
  margin-bottom: 0.75rem;
}
.btn-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.65rem;
}
.aff-btn-wrap {
  display: inline-flex;
  flex-direction: column;
  gap: 0.2rem;
}
a.aff-btn {
  display: inline-block;
  background: var(--btn-bg);
  color: var(--btn-text);
  font-weight: 700;
  font-size: 0.86rem;
  text-decoration: none;
  padding: 0.55rem 0.9rem;
  border-radius: 6px;
}
a.aff-btn:hover { opacity: 0.9; }
.paid-link-tag {
  font-size: 0.73rem;
  color: #ffd166;
  font-weight: 600;
}
footer.site-footer {
  max-width: 1120px;
  margin: 2.5rem auto 0;
  padding: 1.25rem;
  border-top: 1px solid var(--border);
  font-size: 0.82rem;
  color: var(--muted);
}
"""


def build_top_compliance_header(active_path: str) -> str:
    c = BRAND_CONFIG
    s_cls = 'class="active"' if "storage" in active_path else ""
    a_cls = 'class="active"' if "af1" in active_path else ""
    h_cls = 'class="active"' if active_path == "/" else ""
    return f"""\
  <div class="compliance-top-bar" role="region" aria-label="Legal and Affiliate Disclosures">
    <div class="unofficial-banner" id="unofficial-disclaimer-banner">{c['unofficial_disclaimer_banner']}</div>
    <div class="ftc-banner" id="ftc-affiliate-disclosure-banner">{c['ftc_disclosure_banner']}</div>
  </div>
  <header class="site-header">
    <a href="/" class="brand-title">{c['brand_name']}</a>
    <nav class="tool-nav">
      <a href="/" {h_cls}>Hub Home</a>
      <a href="/tools/gta-6-storage-calculator/" {s_cls}>212 GB Storage Calculator</a>
      <a href="/tools/af1-resale-fee-calculator/" {a_cls}>AF1 Resale Fee Comparator</a>
    </nav>
  </header>
"""


def build_index_html() -> str:
    header = build_top_compliance_header("/")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>RigSpec Utility Lab — Console Storage &amp; Sneaker Fee Calculators</title>
  <link rel="stylesheet" href="/assets/styles.css">
</head>
<body>
{header}
  <main class="container">
    <h1>Deterministic Hardware &amp; Resale Engineering Utilities</h1>
    <p class="subtitle">Zero-bloat client-side calculators for console NVMe/Expansion capacity planning and authenticated footwear fee comparison.</p>
    <div class="grid-2">
      <div class="card">
        <h2>Tool 1: Console 212 GB Storage &amp; Download Calculator</h2>
        <p style="color:var(--muted);margin-bottom:1rem;">
          Calculate exact usable SSD deficit on PlayStation 5 (667 GB / 842 GB) and Xbox Series X|S (802 GB / 364 GB) against a 212 GB base install + 300 GB safe-buffer footprint, plus ISP download duration.
        </p>
        <div class="aff-btn-wrap">
          <a class="aff-btn" href="/tools/gta-6-storage-calculator/">Open 212 GB Storage Calculator &rarr;</a>
          <span class="paid-link-tag">Includes static editorial hardware specs &amp; Paid links</span>
        </div>
      </div>
      <div class="card">
        <h2>Tool 2: Cactus Jack x AF1 Resale Fee &amp; Profit Comparator</h2>
        <p style="color:var(--muted);margin-bottom:1rem;">
          Compare exact net seller payout and buyer total across eBay Authenticity Guarantee (~8% fee on &ge; $150 pairs) vs. StockX Level 1 (12% + $5 minimum shipping) for the $130 retail drop.
        </p>
        <div class="aff-btn-wrap">
          <a class="aff-btn" href="/tools/af1-resale-fee-calculator/">Open AF1 Resale Fee Comparator &rarr;</a>
          <span class="paid-link-tag">Includes verified marketplace search Paid links</span>
        </div>
      </div>
    </div>
  </main>
  <footer class="site-footer">
    <p>{BRAND_CONFIG['unofficial_disclaimer_banner']}</p>
    <p style="margin-top:0.4rem;">{BRAND_CONFIG['ftc_disclosure_banner']}</p>
  </footer>
</body>
</html>
"""


def build_storage_calculator_html() -> str:
    header = build_top_compliance_header("/tools/gta-6-storage-calculator/")
    spec_json_inline = json.dumps(STORAGE_SPEC_DATA, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>212 GB Console Storage &amp; Download Time Calculator — RigSpec Utility Lab</title>
  <link rel="stylesheet" href="/assets/styles.css">
</head>
<body>
{header}
  <main class="container">
    <h1>Console 212 GB Storage &amp; ISP Download Time Calculator</h1>
    <p class="subtitle">
      Verify whether your PS5 or Xbox Series X|S usable drive can fit the 212 GB base install (and 300 GB safe day-one patch + shader cache buffer), and compare compatible Gen4 NVMe / Velocity Expansion tiers.
    </p>

    <div class="grid-2">
      <section class="card" aria-label="Storage Inputs">
        <h2>1. Configure Your Console &amp; ISP</h2>
        <label for="console-select">Console Model &amp; Usable OS Capacity</label>
        <select id="console-select"></select>

        <label for="existing-gb">Current Installed Games / Used Space (GB)</label>
        <input type="number" id="existing-gb" min="0" max="2000" step="1" value="480">

        <label for="isp-select">Your Real-World ISP Download Speed (Mbps)</label>
        <select id="isp-select"></select>
      </section>

      <section class="card" aria-label="Storage Telemetry Results">
        <h2>2. Deterministic Storage &amp; Pre-Load Audit</h2>
        <div class="metric-box">
          <div class="metric-label">Remaining Usable Space After 212 GB Base + 300 GB Safe Buffer</div>
          <div class="metric-value" id="deficit-readout">--</div>
          <div style="font-size:0.85rem;color:var(--muted);margin-top:0.25rem;" id="series-s-note"></div>
        </div>
        <div class="metric-box">
          <div class="metric-label">Estimated Uncompressed Pre-Load Download Duration (212 GB)</div>
          <div class="metric-value" id="download-time-readout">--</div>
          <div style="font-size:0.85rem;color:var(--muted);margin-top:0.25rem;" id="buffer-time-note"></div>
        </div>
      </section>
    </div>

    <section class="card" style="margin-top:1.25rem;" aria-label="Compatible Expansion Hardware">
      <h2>3. Compatible Static Editorial Storage Expansion Tiers</h2>
      <p style="font-size:0.85rem;color:var(--muted);margin-bottom:0.8rem;">
        Static editorial specifications only (no live price scraping or price tracking). Every outbound merchant button below is an affiliate link (<strong>Paid link</strong>).
      </p>
      <div class="hardware-list" id="hardware-recommendations"></div>
    </section>
  </main>

  <footer class="site-footer">
    <p>{BRAND_CONFIG['unofficial_disclaimer_banner']}</p>
    <p style="margin-top:0.4rem;">{BRAND_CONFIG['ftc_disclosure_banner']}</p>
  </footer>

  <script id="storage-spec-data" type="application/json">{spec_json_inline}</script>
  <script>
  (function() {{
    const spec = JSON.parse(document.getElementById('storage-spec-data').textContent);
    const consoleSel = document.getElementById('console-select');
    const existingInput = document.getElementById('existing-gb');
    const ispSel = document.getElementById('isp-select');
    const deficitEl = document.getElementById('deficit-readout');
    const seriesSNote = document.getElementById('series-s-note');
    const dlEl = document.getElementById('download-time-readout');
    const bufNote = document.getElementById('buffer-time-note');
    const hwContainer = document.getElementById('hardware-recommendations');

    spec.consoles.forEach(c => {{
      const opt = document.createElement('option');
      opt.value = c.id;
      opt.textContent = c.name + ' (' + c.usable_gb + ' GB Usable)';
      consoleSel.appendChild(opt);
    }});

    spec.isp_speeds_mbps.forEach(s => {{
      const opt = document.createElement('option');
      opt.value = s.mbps;
      opt.textContent = s.label;
      if (s.mbps === 100) opt.selected = true;
      ispSel.appendChild(opt);
    }});

    function formatHours(hours) {{
      const h = Math.floor(hours);
      const m = Math.round((hours - h) * 60);
      if (h >= 24) {{
        const d = Math.floor(h / 24);
        const remH = h % 24;
        return d + 'd ' + remH + 'h ' + m + 'm';
      }}
      return h + 'h ' + m + 'm';
    }}

    function recalculate() {{
      const c = spec.consoles.find(x => x.id === consoleSel.value) || spec.consoles[0];
      const usedGb = Math.max(0, parseFloat(existingInput.value) || 0);
      const freeGb = Math.max(0, c.usable_gb - usedGb);
      const baseDeficit = freeGb - spec.base_install_gb;
      const safeDeficit = freeGb - spec.recommended_total_footprint_gb;

      if (safeDeficit < 0) {{
        deficitEl.className = 'metric-value status-danger';
        deficitEl.textContent = 'DEFICIT: ' + safeDeficit.toFixed(1) + ' GB (Need ' + Math.abs(safeDeficit).toFixed(1) + ' GB freed or expansion)';
      }} else {{
        deficitEl.className = 'metric-value status-ok';
        deficitEl.textContent = '+' + safeDeficit.toFixed(1) + ' GB Clear Above 300 GB Safe Buffer';
      }}

      seriesSNote.textContent =
        'Usable Drive: ' + c.usable_gb + ' GB | Currently Free: ' + freeGb.toFixed(1) + ' GB | ' +
        '212 GB Base consumes ' + c.footprint_pct_of_usable + '% of total usable drive (' + c.buffer_pct_of_usable + '% with 300 GB safe buffer).';

      const mbps = parseFloat(ispSel.value) || 100;
      const megabytesPerSec = (mbps / 8.0) * 0.92; // 92% TCP/HTTP throughput efficiency
      const baseHours = (spec.base_install_gb * 1024.0) / (megabytesPerSec * 3600.0);
      const safeHours = (spec.recommended_total_footprint_gb * 1024.0) / (megabytesPerSec * 3600.0);

      dlEl.textContent = formatHours(baseHours) + ' @ ' + mbps + ' Mbps';
      bufNote.textContent = 'Full 300 GB footprint (base + day-one patch buffer): ' + formatHours(safeHours) + ' effective download window.';

      hwContainer.innerHTML = '';
      c.compatible_tiers.forEach(tierKey => {{
        const hw = spec.editorial_hardware_tiers[tierKey];
        if (!hw) return;
        const div = document.createElement('div');
        div.className = 'hw-item';
        const buttonsHtml = hw.retailers.map(r =>
          '<div class="aff-btn-wrap">' +
            '<a class="aff-btn" href="' + r.url + '" target="_blank" rel="nofollow sponsored noopener">' +
              'Check Spec &amp; Availability at ' + r.merchant +
            '</a>' +
            '<span class="paid-link-tag">(' + r.disclosure + ' — FTC Affiliate Disclosure)</span>' +
          '</div>'
        ).join('');
        div.innerHTML =
          '<div class="hw-title">' + hw.title + ' <span style="color:var(--accent);font-size:0.85rem;">[' + hw.sku_code + ']</span></div>' +
          '<div class="hw-meta">' +
            '<strong>Interface:</strong> ' + hw.interface + ' (' + hw.seq_read_mbs + ' MB/s) &bull; ' +
            '<strong>Fit Check:</strong> ' + hw.form_factor_check + '<br>' +
            '<strong>Editorial Tier:</strong> ' + hw.editorial_tier +
          '</div>' +
          '<div class="btn-row">' + buttonsHtml + '</div>';
        hwContainer.appendChild(div);
      }});
    }}

    consoleSel.addEventListener('change', () => {{
      if (consoleSel.value === 'xbox_series_s') existingInput.value = 180;
      recalculate();
    }});
    existingInput.addEventListener('input', recalculate);
    ispSel.addEventListener('change', recalculate);
    recalculate();
  }})();
  </script>
</body>
</html>
"""


def build_af1_calculator_html() -> str:
    header = build_top_compliance_header("/tools/af1-resale-fee-calculator/")
    spec_json_inline = json.dumps(AF1_RESALE_SPEC_DATA, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Cactus Jack x Air Force 1 Resale Fee &amp; Profit Comparator — RigSpec Utility Lab</title>
  <link rel="stylesheet" href="/assets/styles.css">
</head>
<body>
{header}
  <main class="container">
    <h1>Cactus Jack x Air Force 1 Resale Fee &amp; Net Take-Home Comparator</h1>
    <p class="subtitle">
      Compare seller net payout and buyer all-in cost between eBay Authenticity Guarantee (~8% fee on &ge; $150 sneakers) and StockX Level 1 (9% transaction + 3% processing + $5 shipping) for the Nov 21 SNKRS &amp; Nov 23 CactusJack.com drop.
    </p>

    <div class="grid-2">
      <section class="card" aria-label="Resale Calculator Inputs">
        <h2>1. Select Colorway &amp; Enter Ask Price</h2>
        <label for="sku-select">Release SKU &amp; Colorway</label>
        <select id="sku-select"></select>

        <label for="buy-price">Your Original Retail / Acquisition Cost ($ USD)</label>
        <input type="number" id="buy-price" min="50" max="5000" step="1" value="130">

        <label for="resale-ask">Target Resale Listing Ask ($ USD)</label>
        <input type="number" id="resale-ask" min="80" max="10000" step="5" value="300">

        <label for="seller-ship">Estimated Outbound Shipping / Label Cost ($ USD)</label>
        <input type="number" id="seller-ship" min="0" max="100" step="1" value="0">
      </section>

      <section class="card" aria-label="Marketplace Comparison Results">
        <h2>2. Side-by-Side Net Payout &amp; Fee Delta</h2>
        <div class="metric-box">
          <div class="metric-label">eBay Authenticity Guarantee Net Payout &amp; Profit</div>
          <div class="metric-value status-ok" id="ebay-net-readout">--</div>
          <div style="font-size:0.85rem;color:var(--muted);margin-top:0.25rem;" id="ebay-breakdown"></div>
        </div>
        <div class="metric-box">
          <div class="metric-label">StockX (Level 1: 12% + $5 Min Shipping) Net Payout &amp; Profit</div>
          <div class="metric-value status-warn" id="stockx-net-readout">--</div>
          <div style="font-size:0.85rem;color:var(--muted);margin-top:0.25rem;" id="stockx-breakdown"></div>
        </div>
        <div class="metric-box">
          <div class="metric-label">Fee Advantage (Why Sellers Route &ge; $150 Pairs to eBay)</div>
          <div class="metric-value" id="delta-readout" style="font-size:1.25rem;">--</div>
        </div>
      </section>
    </div>

    <section class="card" style="margin-top:1.25rem;" aria-label="Authenticated Marketplace Links">
      <h2>3. Outbound Authenticated Marketplace Search Links</h2>
      <p style="font-size:0.85rem;color:var(--muted);margin-bottom:0.8rem;">
        Direct search links to authenticated listings only (zero counterfeit marketplaces). Every outbound button below is an affiliate link (<strong>Paid link</strong>).
      </p>
      <div class="btn-row" id="marketplace-links"></div>
    </section>
  </main>

  <footer class="site-footer">
    <p>{BRAND_CONFIG['unofficial_disclaimer_banner']}</p>
    <p style="margin-top:0.4rem;">{BRAND_CONFIG['ftc_disclosure_banner']}</p>
  </footer>

  <script id="af1-spec-data" type="application/json">{spec_json_inline}</script>
  <script>
  (function() {{
    const spec = JSON.parse(document.getElementById('af1-spec-data').textContent);
    const skuSel = document.getElementById('sku-select');
    const buyInput = document.getElementById('buy-price');
    const askInput = document.getElementById('resale-ask');
    const shipInput = document.getElementById('seller-ship');
    const ebayEl = document.getElementById('ebay-net-readout');
    const ebayDesc = document.getElementById('ebay-breakdown');
    const sxEl = document.getElementById('stockx-net-readout');
    const sxDesc = document.getElementById('stockx-breakdown');
    const deltaEl = document.getElementById('delta-readout');
    const linksBox = document.getElementById('marketplace-links');

    spec.sku_catalog.forEach(s => {{
      const opt = document.createElement('option');
      opt.value = s.sku_code;
      opt.textContent = s.sku_code + ' — ' + s.name + ' ($' + s.retail_usd + ' Retail)';
      skuSel.appendChild(opt);
    }});

    function recalc() {{
      const sku = spec.sku_catalog.find(x => x.sku_code === skuSel.value) || spec.sku_catalog[0];
      const buy = Math.max(0, parseFloat(buyInput.value) || 130);
      const ask = Math.max(1, parseFloat(askInput.value) || 300);
      const extraShip = Math.max(0, parseFloat(shipInput.value) || 0);

      // eBay fee calculation
      const ebayRate = ask >= spec.platforms.ebay.fee_threshold_usd
        ? spec.platforms.ebay.fee_rate_above_threshold
        : spec.platforms.ebay.fee_rate_below_threshold;
      const ebayFee = (ask * ebayRate) + spec.platforms.ebay.fixed_order_fee_usd;
      const ebayPayout = ask - ebayFee - extraShip;
      const ebayProfit = ebayPayout - buy;

      // StockX Level 1 calculation (9% tx + 3% proc + $5 min shipping)
      const sxFee = (ask * spec.platforms.stockx.combined_seller_rate) + spec.platforms.stockx.minimum_seller_shipping_usd;
      const sxPayout = ask - sxFee - extraShip;
      const sxProfit = sxPayout - buy;

      const diff = ebayPayout - sxPayout;

      ebayEl.textContent = 'Keep $' + ebayPayout.toFixed(2) + ' (Net Profit: ' + (ebayProfit >= 0 ? '+$' : '-$') + Math.abs(ebayProfit).toFixed(2) + ')';
      ebayDesc.textContent = 'eBay Fee: $' + ebayFee.toFixed(2) + ' (' + (ebayRate * 100).toFixed(1) + '% + $0.40) | Free Authenticity Guarantee Inspection';

      sxEl.textContent = 'Keep $' + sxPayout.toFixed(2) + ' (Net Profit: ' + (sxProfit >= 0 ? '+$' : '-$') + Math.abs(sxProfit).toFixed(2) + ')';
      sxDesc.textContent = 'StockX Fee: $' + sxFee.toFixed(2) + ' (12% combined + $5.00 standard auth shipping)';

      deltaEl.textContent = 'eBay saves you +$' + diff.toFixed(2) + ' per pair (+' + ((diff / ask) * 100).toFixed(1) + '% higher net take-home on a $' + ask.toFixed(0) + ' sale)';

      const ebayUrl = 'https://www.ebay.com/sch/i.html?_nkw=' + sku.ebay_query + '&mkcid=1&mkrid=711-53200-19255-0&campid=5339000000';
      const stockxUrl = 'https://stockx.com/search?s=' + encodeURIComponent(sku.stockx_query);

      linksBox.innerHTML =
        '<div class="aff-btn-wrap">' +
          '<a class="aff-btn" href="' + ebayUrl + '" target="_blank" rel="nofollow sponsored noopener">' +
            'View Authenticated ' + sku.sku_code + ' Listings on eBay' +
          '</a>' +
          '<span class="paid-link-tag">(Paid link — eBay Partner Network Disclosure)</span>' +
        '</div>' +
        '<div class="aff-btn-wrap">' +
          '<a class="aff-btn" href="' + stockxUrl + '" target="_blank" rel="nofollow sponsored noopener">' +
            'Compare Live Bids &amp; Asks for ' + sku.sku_code + ' on StockX' +
          '</a>' +
          '<span class="paid-link-tag">(Paid link — StockX Affiliate Disclosure)</span>' +
        '</div>';
    }}

    skuSel.addEventListener('change', () => {{
      const sku = spec.sku_catalog.find(x => x.sku_code === skuSel.value);
      if (sku) buyInput.value = sku.retail_usd;
      recalc();
    }});
    buyInput.addEventListener('input', recalc);
    askInput.addEventListener('input', recalc);
    shipInput.addEventListener('input', recalc);
    recalc();
  }})();
  </script>
</body>
</html>
"""


def main() -> int:
    files_to_write: dict[str, str] = {
        "assets/styles.css": SHARED_CSS,
        "data/storage_specs.json": json.dumps(STORAGE_SPEC_DATA, indent=2, ensure_ascii=False) + "\n",
        "data/af1_resale_specs.json": json.dumps(AF1_RESALE_SPEC_DATA, indent=2, ensure_ascii=False) + "\n",
        "index.html": build_index_html(),
        "tools/gta-6-storage-calculator/index.html": build_storage_calculator_html(),
        "tools/af1-resale-fee-calculator/index.html": build_af1_calculator_html(),
    }

    assert_governing_invariants(files_to_write)

    manifest_rows = []
    for rel_path, content in files_to_write.items():
        target = DIST_DIR / rel_path
        digest = atomic_write(target, content)
        manifest_rows.append({"file": rel_path, "bytes": target.stat().st_size, "sha256": digest})

    wrangler_toml = """\
name = "console-utility-hub"
compatibility_date = "2026-11-21"
pages_build_output_dir = "dist"
"""
    atomic_write(HUB_ROOT / "wrangler.toml", wrangler_toml)
    atomic_write(DIST_DIR / "BUILD_MANIFEST.json", json.dumps({"files": manifest_rows}, indent=2) + "\n")

    print(json.dumps({"ok": True, "dist_dir": str(DIST_DIR), "files": manifest_rows}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
