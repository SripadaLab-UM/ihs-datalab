---
name: academic-figures
description: Use when creating plots, graphs, visualizations, manuscript figures, thesis figures, posters, paper-ready graphics, or research dashboards.
---

Create research-grade figures that can survive review, publication, and reuse.

Start by choosing the figure target:

1. Exploratory chat answer: small inline chart or quick artifact is fine.
2. Manuscript/thesis figure: create a publication-style figure bundle.
3. Poster/presentation figure: use larger labels, thicker lines, and less detail.

Academic figure bundle

For a manuscript, thesis, poster, or serious analysis output, write these artifacts under `/work/outputs`:

1. `<slug>.svg` or `<slug>.pdf` for vector use.
2. `<slug>.png` at 300-600 dpi for quick preview.
3. `<slug>_caption.md` with a concise title and explanatory caption.
4. `<slug>_source_data.csv` with the plotted values, only when they are aggregates (means, counts, estimates). For plots of individual participants (scatter, spaghetti, strip plots), keep the data in `/work` and name its path in the caption.
5. Source code in `/work/scripts`, such as `/work/scripts/make_<slug>.py` or `/work/scripts/make_<slug>.R` (the user can export them with the outputs).

If the user asks for a dashboard or exploratory data review, make a static HTML report (images and tables, no scripts): DataLab shows and exports web pages with scripts turned off.

Design rules

1. Identify the message before choosing a chart type.
2. Use the simplest chart that supports that message.
3. Do not use 3D, pie, donut, gauge, or decorative chart types unless the user explicitly requests them or they are clearly justified.
4. Do not truncate bar chart axes. If an axis is transformed or clipped, label it clearly and explain why.
5. Show uncertainty when comparing estimates: confidence intervals, credible intervals, standard errors, or distribution marks as appropriate.
6. Report sample sizes, denominators, filters, and missingness when they affect the figure.
7. Prefer direct labels or small multiples over crowded legends.
8. Use restrained grid lines, readable ticks, and no unnecessary backgrounds.
9. Keep captions outside the figure file unless the user asks for a self-contained slide/poster graphic.
10. Message and readability matter more than visual flourish.

Accessibility rules

1. Never rely on color alone; also use marker shape, line style, labels, faceting, or annotations.
2. Use colorblind-safe palettes. Prefer viridis/cividis for continuous scales, Okabe-Ito or ColorBrewer-style qualitative palettes for groups, and diverging palettes only for meaningful centered values.
3. Avoid rainbow/jet colormaps for quantitative data.
4. Use sufficient text/background contrast.
5. Make text legible at final display size: usually 8-12 pt for paper figures, larger for posters/slides.
6. For HTML figures, include a short text summary near each visualization.

Python defaults

Prefer Python for Matplotlib, Seaborn, or Altair (vl-convert is installed for Altair's static export). Plotly can't save static images here. Use explicit figure sizes and save both vector and high-resolution PNG.

Recommended Matplotlib defaults:

```python
import matplotlib.pyplot as plt

plt.rcParams.update({
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "font.size": 9,
    "axes.labelsize": 9,
    "axes.titlesize": 10,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "lines.linewidth": 1.5,
})
```

Use `fig.tight_layout()` or constrained layout, then save:

```python
fig.savefig("/work/outputs/figure_slug.svg", bbox_inches="tight")
fig.savefig("/work/outputs/figure_slug.png", dpi=300, bbox_inches="tight")
```

R defaults

Prefer ggplot2 for R outputs. Save with explicit dimensions and dpi:

```r
ggsave("/work/outputs/figure_slug.pdf", plot = p, width = 6.5, height = 4, units = "in")
ggsave("/work/outputs/figure_slug.png", plot = p, width = 6.5, height = 4, units = "in", dpi = 300)
```

Captions

Write captions that let a reader understand the figure without the chat transcript:

1. Start with a short descriptive title.
2. State what is plotted and the population/sample.
3. Define units, groups, panels, intervals, and abbreviations.
4. State important filters/exclusions or transformations.
5. Avoid making causal claims unless the design supports them.

Final response

When you create academic figures, list the artifact paths and say which file is best for manuscript use, which is best for preview, and where the source code/source data are saved.
