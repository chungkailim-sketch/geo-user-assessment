"""The full GEO audit behind the emailed PDF report.

The quick audit scores Citability and Authority. The full audit adds the other categories of the GEO
skill's audit (geo.zip `references/modules/audit.md`): Content E-E-A-T, Technical GEO (incl. AI crawler
access and llms.txt), Schema & Structured Data and Platform Optimization, and combines all six into the
composite GEO Score:

    GEO = Citability×0.25 + Brand Authority×0.20 + E-E-A-T×0.20 + Technical×0.15 + Schema×0.10 + Platform×0.10

Checks that can't be measured from outside the site (e.g. Bing Webmaster Tools, field Core Web Vitals) are
marked as not measured and left out of their category's denominator rather than scored as failures.
"""
