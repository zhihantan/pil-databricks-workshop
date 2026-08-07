# Container photo attribution & licenses

The images in this directory are **real container photographs** bundled so the
Container Analysis agent can be demonstrated on genuine photos (not just the
synthetic diagrams). They are committed to this **public** repository, so each
image must be cleared for redistribution and attributed per its license.

Most are sourced from **Wikimedia Commons** under CC / public-domain licenses,
with the source page, author, and license verified and recorded below. A few
predate the Commons crawl and still need their provenance confirmed (marked
_TODO_).

> ⚠️ **Attribution obligations.** CC BY / CC BY-SA licenses *require* crediting
> the author, naming the license, and linking to the source. The rows below
> carry that information. Before public release, confirm the _TODO_ rows (or
> remove those images) and keep this file alongside the photos.

## Damaged containers (ground truth: major)

| File | Source (Wikimedia Commons) | Author | License |
|------|----------------------------|--------|---------|
| `real_damaged_02_bremerhaven.jpg` | [Damaged container - Port of Bremerhaven - 2011](https://commons.wikimedia.org/wiki/File:Damaged_container_-_Port_of_Bremerhaven_-_2011.png) | Buonasera | CC BY-SA 3.0 |
| `real_damaged_03_broken_reefer.jpg` | [Broken container on reefer](https://commons.wikimedia.org/wiki/File:Broken_container_on_reefer.jpg) | Hervé Cozanet | CC BY-SA 3.0 |
| `real_damaged_04_crushed_wall.jpg` | [Containerschaden2](https://commons.wikimedia.org/wiki/File:Containerschaden2.jpg) | GeorgHH | Public domain |
| `real_damaged_05_breached.jpg` | [Damaged shipping container](https://commons.wikimedia.org/wiki/File:Damaged_shipping_container.jpg) | neurmadic aesthetic (Todd Anderson) | CC BY-SA 2.0 |
| `real_damaged_06_collapsed.jpg` | [Container Empire front](https://commons.wikimedia.org/wiki/File:Container_Empire_front.JPG) | Wusel007 | CC BY-SA 3.0 |
| `real_damaged_01_crushed.jpg` | _TODO — predates the Commons crawl; confirm source_ | _TODO_ | _TODO_ |

## Clean / intact containers (ground truth: none)

| File | Source (Wikimedia Commons) | Author | License |
|------|----------------------------|--------|---------|
| `real_clean_04_maersk_rail.jpg` | [Maersk 42G1 MRKU 046963 0](https://commons.wikimedia.org/wiki/File:Maersk_42G1_MRKU_046963_0.jpg) | Col André Kritzinger | CC BY-SA 3.0 |
| `real_clean_05_maersk_side.jpg` | [Maersk 45G1 MRKU 282738 9](https://commons.wikimedia.org/wiki/File:Maersk_45G1_MRKU_282738_9.jpg) | Col André Kritzinger | CC BY-SA 3.0 |
| `real_clean_06_maersk_doors.jpg` | [Maersk 22G1 MRKU 755687 0](https://commons.wikimedia.org/wiki/File:Maersk_22G1_MRKU_755687_0.jpg) | Col André Kritzinger | CC BY-SA 3.0 |
| `real_clean_07_maersk_reefer.jpg` | [Maersk 45R1 MNBU 321737 5](https://commons.wikimedia.org/wiki/File:Maersk_45R1_MNBU_321737_5.jpg) | Col André Kritzinger | CC BY-SA 3.0 |
| `real_clean_01_blue_grey.jpg` | _TODO — predates the Commons crawl; confirm source_ | _TODO_ | _TODO_ |
| `real_clean_02_40ft_docks.jpg` | _TODO — predates the Commons crawl; confirm source_ | _TODO_ | _TODO_ |
| `real_clean_03_terminal.jpg` | _TODO — predates the Commons crawl; confirm source_ | _TODO_ | _TODO_ |

## Notes

- Ground-truth damage labels live in `labels.json` (same schema as the synthetic
  generator: `file_name`, `container_no`, `gt_damage`, `gt_damage_type`).
- Images were downscaled to ≤ 1600 px on the long edge and re-encoded as JPEG to
  keep the repository lean and stay under the FMAPI vision endpoint's base64
  size limit. The originals remain at their Wikimedia Commons source pages above.
- CC BY-SA licenses are share-alike: adaptations must carry the same license.
  These photos are bundled unmodified (aside from downscaling) for demonstration.
- If you cannot confirm a _TODO_ row, the safest options are to replace it with a
  photo you own / one under a clearly-permissive license, or drop it.
