# Container photo attribution & licenses

The images in this directory are **real container photographs** bundled so the
Container Analysis agent can be demonstrated on genuine photos (not just the
synthetic diagrams). They are committed to this **public** repository, so each
image must be cleared for redistribution and attributed per its license.

> ⚠️ **ACTION REQUIRED before public release / distribution.** Fill in the
> `Source`, `Author`, and `License` for every image below. Most permissive
> licenses (e.g. **CC BY 4.0**) *require* attribution — author + license +
> a link to the source and the license deed. Remove or replace any image whose
> license you cannot confirm allows redistribution. Until every row is verified,
> treat this set as internal-demo-only.

## Images

| File | Damage (ground truth) | Source (URL) | Author | License |
|------|----------------------|--------------|--------|---------|
| `real_damaged_01_crushed.jpg` | major | _TODO_ | _TODO_ | _TODO_ |
| `real_damaged_02_bremerhaven.jpg` | major | _TODO_ | _TODO_ | _TODO_ |
| `real_damaged_03_broken_reefer.jpg` | major | _TODO_ | _TODO_ | _TODO_ |
| `real_clean_01_blue_grey.jpg` | none | _TODO_ | _TODO_ | _TODO_ |
| `real_clean_02_40ft_docks.jpg` | none | _TODO_ | _TODO_ | _TODO_ |
| `real_clean_03_terminal.jpg` | none | _TODO_ | _TODO_ | _TODO_ |

## Notes

- Ground-truth damage labels live in `labels.json` (same schema as the synthetic
  generator: `file_name`, `container_no`, `gt_damage`, `gt_damage_type`).
- Images were downscaled to ≤ 1600 px on the long edge and re-encoded as JPEG to
  keep the repository lean and stay under the FMAPI vision endpoint's base64
  size limit. The originals are not stored here.
- If you cannot attribute an image, the safest options are: (a) replace it with a
  photo you own or one under a clearly-permissive license, or (b) drop it — the
  synthetic set still provides the labeled accuracy baseline.
