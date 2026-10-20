# Large packet extraction feasibility

Measured locally on 2026-10-19 with `scripts/benchmark_pdf.py` and installed Poppler. This is a single machine measurement, not a Cloud Run performance estimate.

The [official October 6 South Lake Tahoe packet](https://d3n9y02raazwpg.cloudfront.net/slt/56c75fd5-9a53-11f1-bb61-005056a89546-5c7c4fe4-84cb-4317-b8ef-0c329738dcd2-1790799016.pdf) downloaded into a temporary directory, was extracted with `pdftotext -layout`, and was removed after measurement.

| Measurement | Result |
| --- | ---: |
| Original PDF | 38,079,276 bytes |
| Pages | 505 |
| Extracted text | 1,219,430 bytes |
| Pages with no text | 0 |
| Download time | 1.040 seconds |
| Extraction time | 0.462 seconds |
| Extraction child peak RSS | 20,037,632 bytes |
| Model calls / billed API usage | 0 |

SHA-256: `26c7e5d6b4930ffa85006bd1a0f2200720da06d0aedd29fefd07147972e73980`.

Poppler emitted eight `Invalid Font Weight` warnings but completed successfully. Nonblank text does not establish extraction quality for diagrams, scans, tables, or substantive claims. The proof of concept publishes agenda excerpts only; neither this benchmark nor the fixtures claim complete analysis of this packet. Model quality/cost benchmarking remains deferred until a provider is selected.

Reproduce from the repo root:

```sh
python3 scripts/benchmark_pdf.py 'https://d3n9y02raazwpg.cloudfront.net/slt/56c75fd5-9a53-11f1-bb61-005056a89546-5c7c4fe4-84cb-4317-b8ef-0c329738dcd2-1790799016.pdf'
```

The benchmark caps downloads at 128 MiB and extraction at 180 seconds. The ingestion worker has its own documented processing bounds.
