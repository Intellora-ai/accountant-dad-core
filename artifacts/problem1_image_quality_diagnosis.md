# Problem 1 - can preprocessing recover the unreadable documents?

DIAGNOSIS ONLY. No production code changed.

Documents with fewer than 5 legible OCR rows: **21**

| document | size | mode | bytes | rows | brightness | stdev | spread | edges |
|---|---|---|---|---|---|---|---|---|
| open-datasets-and-photos-002.jpg | 837x292 | RGB | 82KB | 2 | 244 | 42.1 | 190 | 11.07 |
| open-datasets-and-photos-006.jpg | 2200x1221 | RGB | 424KB | 0 | 129 | 65.0 | 255 | 12.09 |
| open-datasets-and-photos-007.jpg | 2428x3090 | RGB | 1976KB | 4 | 157 | 63.2 | 252 | 9.91 |
| open-datasets-and-photos-011.jpg | 1650x1300 | RGB | 107KB | 0 | 58 | 88.6 | 255 | 1.78 |
| open-datasets-and-photos-014.jpg | 288x338 | RGB | 66KB | 0 | 100 | 59.2 | 254 | 26.63 |
| open-datasets-and-photos-015.jpg | 798x512 | RGB | 99KB | 0 | 200 | 54.1 | 254 | 41.45 |
| open-datasets-and-photos-016.jpg | 277x182 | RGB | 9KB | 0 | 128 | 68.8 | 254 | 48.72 |
| open-datasets-and-photos-020.jpg | 739x1400 | RGB | 367KB | 2 | 129 | 67.0 | 212 | 13.72 |
| open-datasets-and-photos-027.jpg | 807x1826 | RGB | 377KB | 0 | 162 | 81.5 | 246 | 6.81 |
| open-datasets-and-photos-029.jpg | 942x1866 | RGB | 412KB | 0 | 154 | 69.7 | 234 | 6.30 |
| open-datasets-and-photos-030.jpg | 1132x2137 | RGB | 404KB | 0 | 121 | 58.2 | 195 | 3.40 |
| open-datasets-and-photos-031.jpg | 1217x2186 | RGB | 588KB | 0 | 124 | 59.3 | 211 | 7.87 |
| open-datasets-and-photos-034.jpg | 2202x1313 | RGB | 610KB | 2 | 115 | 55.7 | 254 | 6.19 |
| open-datasets-and-photos-035.jpg | 623x1296 | RGB | 202KB | 3 | 196 | 61.8 | 231 | 13.05 |
| open-datasets-and-photos-037.jpg | 1004x446 | RGB | 124KB | 0 | 232 | 41.4 | 181 | 16.66 |
| open-datasets-and-photos-039.jpg | 365x753 | RGB | 257KB | 0 | 203 | 68.1 | 255 | 40.44 |
| open-datasets-and-photos-046.jpg | 3264x2448 | RGB | 2167KB | 1 | 147 | 63.4 | 255 | 10.46 |
| open-datasets-and-photos-047.jpg | 601x1280 | RGB | 63KB | 4 | 153 | 16.9 | 149 | 5.87 |
| open-datasets-and-photos-048.jpg | 552x1280 | RGB | 92KB | 0 | 152 | 37.5 | 176 | 12.44 |
| open-datasets-and-photos-054.jpg | 2023x2222 | RGB | 2459KB | 3 | 146 | 37.6 | 236 | 8.60 |
| open-datasets-and-photos-055.jpg | 189x85 | RGB | 7KB | 0 | 222 | 24.2 | 153 | 34.86 |

## Trials, one method at a time

| document | method | rows before | rows after | labels recovered | ms |
|---|---|---|---|---|---|
| open-datasets-and-photos-002.jpg | original | 2 | 2 | - | 60 |
| open-datasets-and-photos-002.jpg | grayscale | 2 | 4 | - | 54 |
| open-datasets-and-photos-002.jpg | contrast_x2 | 2 | 4 | - | 53 |
| open-datasets-and-photos-002.jpg | upscale_x2 | 2 | 4 | - | 80 |
| open-datasets-and-photos-002.jpg | upscale_x3 | 2 | 4 | - | 121 |
| open-datasets-and-photos-002.jpg | threshold_128 | 2 | 4 | - | 49 |
| open-datasets-and-photos-002.jpg | threshold_mean | 2 | 9 | - | 57 |
| open-datasets-and-photos-002.jpg | sharpen | 2 | 3 | - | 54 |
| open-datasets-and-photos-002.jpg | denoise_median | 2 | 3 | - | 57 |
| open-datasets-and-photos-002.jpg | upscale_x2_contrast | 2 | 4 | - | 78 |
| open-datasets-and-photos-006.jpg | original | 0 | 3 | - | 393 |
| open-datasets-and-photos-006.jpg | grayscale | 0 | 0 | - | 227 |
| open-datasets-and-photos-006.jpg | contrast_x2 | 0 | 4 | - | 219 |
| open-datasets-and-photos-006.jpg | upscale_x2 | 0 | 18 | - | 823 |
| open-datasets-and-photos-006.jpg | upscale_x3 | 0 | 22 | - | 1579 |
| open-datasets-and-photos-006.jpg | threshold_128 | 0 | 10 | - | 131 |
| open-datasets-and-photos-006.jpg | threshold_mean | 0 | 10 | - | 168 |
| open-datasets-and-photos-006.jpg | sharpen | 0 | 11 | - | 246 |
| open-datasets-and-photos-006.jpg | denoise_median | 0 | 0 | - | 283 |
| open-datasets-and-photos-006.jpg | upscale_x2_contrast | 0 | 32 | - | 828 |
| open-datasets-and-photos-007.jpg | original | 4 | 0 | - | 1330 |
| open-datasets-and-photos-007.jpg | grayscale | 4 | 115 | - | 912 |
| open-datasets-and-photos-007.jpg | contrast_x2 | 4 | 82 | - | 720 |
| open-datasets-and-photos-007.jpg | upscale_x2 | 4 | 102 | - | 2671 |
| open-datasets-and-photos-007.jpg | upscale_x3 | 4 | 133 | - | 5266 |
| open-datasets-and-photos-007.jpg | threshold_128 | 4 | 118 | - | 484 |
| open-datasets-and-photos-007.jpg | threshold_mean | 4 | 73 | - | 436 |
| open-datasets-and-photos-007.jpg | sharpen | 4 | 81 | - | 842 |
| open-datasets-and-photos-007.jpg | denoise_median | 4 | 89 | - | 1152 |
| open-datasets-and-photos-007.jpg | upscale_x2_contrast | 4 | 65 | - | 2176 |
| open-datasets-and-photos-011.jpg | original | 0 | 0 | - | 164 |
| open-datasets-and-photos-011.jpg | grayscale | 0 | 0 | - | 93 |
| open-datasets-and-photos-011.jpg | contrast_x2 | 0 | 0 | - | 76 |
| open-datasets-and-photos-011.jpg | upscale_x2 | 0 | 0 | - | 282 |
| open-datasets-and-photos-011.jpg | upscale_x3 | 0 | 0 | - | 581 |
| open-datasets-and-photos-011.jpg | threshold_128 | 0 | 0 | - | 74 |
| open-datasets-and-photos-011.jpg | threshold_mean | 0 | 0 | - | 79 |
| open-datasets-and-photos-011.jpg | sharpen | 0 | 0 | - | 105 |
| open-datasets-and-photos-011.jpg | denoise_median | 0 | 0 | - | 120 |
| open-datasets-and-photos-011.jpg | upscale_x2_contrast | 0 | 0 | - | 197 |

## Total legible rows per method across the sample

- `upscale_x3` 159
- `threshold_128` 132
- `upscale_x2` 124
- `grayscale` 119
- `upscale_x2_contrast` 101
- `sharpen` 95
- `threshold_mean` 92
- `denoise_median` 92
- `contrast_x2` 90
- `original` 5
