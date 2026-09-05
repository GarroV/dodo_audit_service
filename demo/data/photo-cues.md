# Photo cue map (demo)

This is a synthetic cue map for the DEMO dataset only. It maps "what you see
on the frame" to a DEMO checklist item (`DEM01`..`DEM10`) so the fast path can
offer an item without calling the model. It does not describe any real audit
methodology and must never be confused with one.

## Class thresholds that come up most often

Keep this in view: for most calls there is no need to open `criteria.md`.

| Item | D1 | D2 | D3 |
|---|---|---|---|
| DEM01 waste bins | lid left open | bin overflowing, or waste on the ground | — |
| DEM03 dining furniture | one table or chair | three or more, or visible damage | — |
| DEM05 shelf life | one item, opened today | two or three items | four or more items, or already served |
| DEM06 work surfaces | one board between food types | two or more, or raw meat residue | — |
| DEM07 dry goods | one box on the floor | a whole pallet on the floor | — |
| DEM09 staff uniform | one person, minor stain | two or more, or unfit for the shift | — |
| DEM10 handwashing sink | one item missing | sink blocked, or no hot water | — |

For every other item the class is the only one on its list, so there is nothing to choose.

## Storefront / entrance

| What you see | Item |
|---|---|
| open bin lid | DEM01 |
| overflowing waste bin | DEM01 |
| broken bin lid | DEM01 |
| entrance door glass | DEM02 |
| smudges on the door handle | DEM02 |
| scratched entrance handle | DEM02 |

## Dining area / counter

| What you see | Item |
|---|---|
| crumbs left on the table | DEM03 |
| sticky residue on chair | DEM03 |
| wobbly chair leg | DEM03 |
| spilled drink on tabletop | DEM03 |
| outdated price on menu board | DEM04 |
| expired promo poster | DEM04 |
| old sticker on price display | DEM04 |

## Kitchen line

| What you see | Item |
|---|---|
| expired product label | DEM05 |
| product past shelf life date | DEM05 |
| unlabeled product in prep area | DEM05 |
| food residue on cutting board | DEM06 |
| raw meat residue on prep surface | DEM06 |
| unsanitized board between orders | DEM06 |

## Storage / dry goods

| What you see | Item |
|---|---|
| boxes stacked on the floor | DEM07 |
| shelf pushed against the wall | DEM07 |
| dry goods below shelf height | DEM07 |
| open container without date label | DEM08 |
| faded date label on the container | DEM08 |
| missing date sticker on jar | DEM08 |

## Staff area

| What you see | Item |
|---|---|
| stained uniform | DEM09 |
| missing name badge | DEM09 |
| torn apron | DEM09 |
| empty soap dispenser | DEM10 |
| no paper towels at the sink | DEM10 |
| blocked handwashing sink | DEM10 |
