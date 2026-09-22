# Figures for the delivery documents

| File | Used in | How it is made |
|---|---|---|
| `fig1-v3-pipeline.svg/.png` | memory, Figure 1 | `uv run --with cairosvg python docs/fmp-delivery/figures/make_figures.py` |
| `fig3-rav.svg/.png` | memory, Figure 3 | same script |
| `figA7-writeback-models.png` | appendices, Figure A.7 | see below |

Figure A.7 is the django-extensions model graph of the `writeback` app,
regenerated on 2026-09-16 after the write-path rewrite added eight columns to
`ModificationProposal`. The graph template asks for the Roboto font, which is
not installed on the build machine; with it missing, Graphviz measures the
boxes wrongly and they overlap. The font is swapped for DejaVu Sans and the
graph is laid out with neato:

```bash
cd src
uv run python manage.py graph_models writeback -X UUIDModel,TimestampedModel --dot -o /tmp/figA7.dot
sed 's/Roboto/DejaVu Sans/g' /tmp/figA7.dot > /tmp/figA7-dejavu.dot
uv run python -c "
import pygraphviz as pgv
g = pgv.AGraph('/tmp/figA7-dejavu.dot')
g.graph_attr.update(overlap='false', splines='true', sep='+20')
g.draw('../docs/fmp-delivery/figures/figA7-writeback-models.png', prog='neato', args='-Gdpi=160')
"
```
