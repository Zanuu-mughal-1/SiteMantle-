# SiteMantle brand assets

The product mark combines a geometric mantle shield with directional cuts: a compact visual for directing site builds, discovering pages, and elevating web quality. The wordmark keeps Site in black and Mantle in red.

- `sitemantle-logo.png`: horizontal mark and wordmark, transparent background.
- `sitemantle-banner.png`: white documentation masthead with the line “Your complete website director & SEO house.”

Use the transparent logo on a light field. Keep its proportions and give it clear space on every side. The white masthead is suitable for a README in either a light or dark viewer. Avoid shadows, recoloring or placing the dark wordmark on a dark background.

The visual palette uses white, near-black (`#151515`) and restrained red (`#991B1B`). Set accompanying text in a clean sans serif with readable spacing. The brand belongs to Zain ul Abideen; see the repository license for reuse terms.

The report renderer packages a byte-identical copy of the logo in `src/sitemantle/report_assets/` so installed wheels can export reports offline. A regression check prevents that copy from drifting. Report styling uses ink `#151515`, dark red `#991B1B`, white and neutral paper `#F5F3F1`; see [the report guide](../docs/branded-reports.md).
