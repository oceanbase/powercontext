# Knowledge-base parser contract v1

[Issue #2849](https://github.com/oceanbase/powercontext/issues/2849) defines this contract within
[the parent acceptance matrix](https://github.com/oceanbase/powercontext/issues/2848). The evaluation
manifest is ready for maintainer freeze after validation. It contains the dataset, generator, expected-output and
scorer hashes, the pinned retrieval model, and the threshold provenance.

## Results and reuse

The parser produces a JSON `ParsedDocument` with ordered text blocks, optional heading paths and
Markdown reference definitions, source locators, and coverage. Its identity hashes the source byte
digest, canonical format, parser name and version, parser configuration digest, and locator version.
Extension aliases for Markdown and JPEG share their respective format identities. A parser change
that affects output increments its version or configuration identity. The existing adapters hash their
output code and relevant dependency versions. A locator interpretation change
requires a new locator version. Chunk size and overlap are independent of the parse identity.

The parse key includes the parser and model identity, locator mapping version, configuration,
format and immutable source digest. Equal keys are eligible for reuse. Different or missing keys
require parsing. Chunking and embedding settings are downstream inputs. The Worker integration in
#2850 owns persistent cache read, write and recovery.

New processing requests pin the parser profile. A Worker rejects a pending request when its pinned
profile differs from the installed one. Requests already accepted without a profile use the
installed profile. Each new parsed revision records `parser_profile`, `parsed_result_id` and
`parse_coverage`. Existing revisions keep their original chunk positions and identities.
An ordinary import of identical bytes under a different recorded profile requires explicit
reprocessing. A new explicit reprocess request uses the current profile and preserves the
existing revision precondition and protections for manual edits. An ordinary repeat import can
keep the existing revision only when its recorded parse key and chunking settings match.
A missing or different parse key requires parsing, subject to the same manual-edit protection.

Markdown chunk output retains its one-based inclusive line fields. The legacy
`block_char_start` and `block_char_end` address the packed block before appended references.
The plugin's existing parse entry point continues to return the legacy chunk shape.

Coverage counts use the declared unit. A known total must be at least the covered count. Partial or
unknown coverage requires a warning. Existing OCR reports recognized blocks and carries
`coverage_unknown` because the expected number of readable regions in a source image is unknown.
Warning codes and features are structured. The current OCR processing state remains compatible
with existing clients, with the coverage report available on the published revision.

## Source locators

- Every index is zero-based and every range is half-open. Characters are Unicode code points after
  decoding and removing an encoding BOM. No normalization is applied when deriving offsets.
- Markdown lines follow CommonMark CR, LF, and CRLF handling. CSV rows count logical records,
  including a header if present. Embedded line breaks in a quoted field do not advance the record.
- Paragraph, table, page, slide, sheet, and OCR block numbers follow original source order. For
  DOCX, top-level paragraphs and tables use independent ordinals. Slides include hidden slides.
  A shape path lists child ordinals at each level of grouping. Grid locators use source row and
  column ranges, including gaps.
- Geometry stores native polygon points and the native bounding box as
  `[min_x, min_y, max_x, max_y]`. Native image coordinates use decoded pixels with the top-left
  origin. Native slide coordinates use EMU with the top-left origin. Native PDF page coordinates
  use points in the unrotated page with the bottom-left origin and a declared crop or media box.
  The affine map `[a,b,c,d,e,f]` produces `(a*x+b*y+c, d*x+e*y+f)` in the displayed reference
  box after crop and rotation, with a top-left origin and both axes in the closed interval
  `[0,1]`. An out-of-box native polygon must be handled by the parser before constructing its
  locator. The complete JSON retains the transform and native geometry.
- For Prepared Context, `search_artifacts` projects existing Markdown line ranges and historical
  OCR coordinates into `source_locator`. Markdown ranges become zero-based and half-open.
  For OCR, the stored pixel polygon is normalized using the stored image dimensions.
  `serialize_context_locator` limits each typed locator to 1,024 UTF-8 bytes and each polygon to
  16 points rounded to eight decimals. The projection retains valid `block_char_start` and
  `block_char_end` as half-open offsets locating the chunk's slice in the parsed or packed block
  text. An incomplete or invalid historical locator leaves
  the text and chunk reference available. Raw geometry and OCR confidence remain available
  through existing chunk reads. The structured hit and the passage carry the same projection.

## Adapter entry point

A format module exports a `ParserAdapter` with a `FormatCapability`, a profile factory, and a
callback receiving source bytes and its `ResourceLimits`. The callback returns `ParserOutput`
with ordered blocks and coverage. The adapter checks input and extracted-text limits and attaches
the source digest and profile. A format with a page or slide limit provides a count callback.
`preflight(data)` verifies exact source totals before extraction. Missing required totals and
over-limit totals raise `ContentError`. DOCX supplies an actual count from the fixed renderer.
PPTX counts hidden slides. When page or slide coverage is reported, its total must agree with
preflight even if extraction covers only part of the file. Format-specific expansion, page, and grid limits are enforced by
the callback. Module tests can call `adapter.parse_document(data)` and pass its result to
`chunk_document` for every locator kind. Each chunk keeps the source locator separately from the
half-open slice of the block text. Markdown keeps its legacy packing behavior. Integration adds
the adapter to `build_parser_registry`, which rejects duplicate extensions.

Only the Markdown and standalone-image adapters are registered. The integration order proposed
for maintainers is to merge the #2849 foundation after sign-off, then rebase #2832 onto its
`ParserAdapter`, `ParsedDocument`, profile and context projection. #2832 can continue in parallel. Its merge must conform to the frozen protocol, including the
profile, locator, coverage and Prepared Context rules. The hard start gate for #2851–#2855
requires #2849 to close and a maintainer-confirmed freeze record.
[HTML pull request #2832](https://github.com/oceanbase/powercontext/pull/2832)

## Locked dependencies

| Format | Parser stack | Runtime dependency and licence |
| --- | --- | --- |
| Markdown and standalone images | Existing Markdown-It-Py 4.2.0, RapidOCR 3.10.0, Pillow 12.3.0 and ONNX Runtime 1.32.0 on the checked lock | Markdown-It-Py and mdurl MIT. RapidOCR and its bundled ONNX models Apache-2.0. Pillow MIT-CMU. ONNX Runtime MIT with bundled notices. |
| TXT and CSV | Python standard library codecs and csv | PSF-2.0. |
| PDF | `pypdfium2==5.15.2` with PDFium 159.0.8149.0 for text, geometry and page rasterization | No additional Python runtime packages. Wrapper Apache-2.0 or BSD-3-Clause, with separate permissive native notices. |
| DOCX, XLSX, PPTX | Bounded standard-library ZIP reader and `defusedxml==0.7.1` for XML. DOCX requires a fixed renderer/font environment for page count. | No additional Python runtime packages for defusedxml. Its package includes the PSF license. Renderer and fonts are optional deployment dependencies. |

The root lock pins `knowledge-base-formats` to the two exact Python packages above.
`knowledge-base` directly declares Markdown-It-Py. [The artifact list](dependencies.json)
records exact wheel URLs and SHA-256 hashes, including Linux x86-64/arm64, macOS x86-64/arm64
and Windows PDFium wheels.
For a selected platform, download its compatible wheels and install from verified wheel bytes.
This isolated check exercises the two format packages.

```sh
uv lock --check
uv lock --project evaluation --check
uv lock --project e2e/bub --check
uv venv --python 3.12.14 /tmp/kb-dependency-probe
uv pip install --python /tmp/kb-dependency-probe/bin/python --offline --no-index --only-binary :all: \
  --find-links /path/to/verified-wheelhouse defusedxml==0.7.1 pypdfium2==5.15.2
/tmp/kb-dependency-probe/bin/python -c 'import defusedxml, pypdfium2'
```

[pypdfium2 package](https://pypi.org/project/pypdfium2/5.15.2/) ·
[defusedxml package](https://pypi.org/project/defusedxml/0.7.1/)

The macOS arm64 Python 3.12 Markdown/OCR dependency closure also includes OpenCV headless
5.0.0.93 (Apache-2.0), NumPy 2.5.1 (BSD-3-Clause, 0BSD, MIT, Zlib and CC0-1.0), Shapely
2.2.0 (BSD-3-Clause with GEOS notices), pyclipper 1.4.0 (MIT), PyYAML 6.0.3 (MIT),
OmegaConf 2.4.0 (BSD with ANTLR notices), six 1.17.0 (MIT), tqdm 4.70.1 (MPL-2.0 and
MIT), requests 2.34.2 (Apache-2.0), colorlog 6.12.0 (MIT), packaging 26.3 (Apache-2.0 or
BSD-2-Clause), flatbuffers 25.12.19 (Apache-2.0), protobuf 7.35.1 (BSD-3-Clause), certifi
2026.10.19 (MPL-2.0), charset-normalizer 3.4.9 (MIT), idna 3.18 (BSD-3-Clause), and
urllib3 2.8.0 (MIT). No AGPL runtime dependency was found in the inspected Python parser graph.

RapidOCR 3.10.0's PyPI wheel SHA-256 is
`2fc34e26cd0f48514804a94f8832283039c7334076bff60768b1fed9ad068050`. An intact verified wheel can
run without network access. The existing RapidOCR adapter uses its installed default configuration. Its loader may attempt
a download if a bundled model is missing or damaged. The model hashes below identify the
locked evaluation installation and its parse profile.
[RapidOCR package](https://pypi.org/project/rapidocr/3.10.0/)

| Bundled artifact | SHA-256 |
| --- | --- |
| config.yaml | `06623941e188461a0bc263fe16175cb5e5f6f6b01f4387e546d3a741569fc021` |
| PP-OCRv6_det_small.onnx | `090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f` |
| PP-OCRv6_rec_small.onnx | `6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884` |
| ch_ppocr_mobile_v2.0_cls_mobile.onnx | `e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c` |

DOCX page-count execution requires a separate deployment environment. LibreOffice 26.8.3 (MPL-2.0 distribution
with upstream third-party notices) has a Linux x86-64 DEB archive SHA-256 of
`e7cd3538e85818ff9dd9bb65386769fde927011242aace5a144989f4000dcabf`. Review must still fix and
execute the OS/renderer/font bundle together before DOCX page-count acceptance. The future Worker
must return a deterministic failure if the configured renderer cannot verify its page count.

## Expected-output provenance

The synthetic corpus specifies text and labels in `fixtures.py`. Its generator writes literal
source text, ZIP/XML records and PDF drawing instructions. It reads pinned raster assets.
Expected labels are written from the authored source and the locator rules above. Generation
imports no knowledge-base parser or OCR engine.

An independent verifier read the source samples and transcribed the rendered rasters without
running the knowledge-base parser. A dark-pixel bounding box (intensity < 128) was used as a
geometric cross-check, and PDFium rendered the PDF at scale 4 to validate the page transform.
The checked values below fall inside the authored ranges in the manifest.

| Source | Independent check |
| --- | --- |
| `service.md` | Heading and text on zero-based lines 0 and 2. |
| `cooling.txt` | Seven code points after the BOM and before the final LF. The emoji occupies code point 2. |
| `drainage.pdf` | Visible text “Emergency drainage” at normalized center (0.5385, 0.4201). Scan “Close the drainage gate” at (0.3023, 0.5188). The scan placement also follows from the PDF draw matrix and page bounds. |
| `meter.jpg` | “Inspect the pressure gauge” at (0.3507, 0.4833). |
| `vault.png` | “Rotate the backup token” at (0.3167, 0.4833). |
| `radio.jpeg` | “Calibrate the receiver” at (0.2778, 0.4583). |
| `filter.webp` | “Clean the intake filter” at (0.2757, 0.4583). |
| `seal.docx` | Raw package XML places “Replace the valve seal” in the first paragraph and “Gasket” in the first table cell. |
| `stock.xlsx` | Raw package XML places “Copper inventory” and numeric 17 at sheet 0, cells A1 and B1. |
| `wind.pptx` | Raw package XML declares the first slide hidden and contains “Wind turbine inspection” in its first shape. |
| `shift.csv` | The comma in “Stow tools, close bay” is inside a quoted field on logical record 1. |

The visual checks verify source text and geometry. Coverage labels come from the authored source
units and the protocol's explicit unknown-total rule for standalone OCR. Each retrieval query
has one relevant source file, chosen by reading the source content. Exact order, locator anchors
and coverage are acceptance requirements for these controlled samples. Text, locator and coverage scoring require an exact match against the independently authored
label at the scorer's normalization and geometric tolerance. The six existing-format chunk cases
are checked separately against exact output hashes exported from the original parser. Retrieval
thresholds remain provisional until #2856 measures the fixed embedding model.

## Evaluation commands

Run from the repository root with CPython 3.12.14 and Unicode 15.0.0. The verifier checks both,
the installed parser, format and embedding package versions, and the bundled OCR configuration
and model hashes. It compares each registered extension's complete parser profile against the
manifest. This catches changes in profile inputs such as Markdown's URL library, OCR's native
dependencies, model identity and locator mapping. The manifest
hashes the checked-in encoded raster assets, whose generation OS, architecture, Pillow, font
renderer and codec versions are recorded. Replay copies the source image bytes and decodes the
grayscale raster for the scan-only PDF page. This keeps the corpus bytes independent of the host
Pillow build. The evaluator hashes UTF-8 text inputs with LF normalization, including on CRLF checkouts.

The manifest covers
eleven synthetic files under Apache-2.0, including
one PDF with a rotated, cropped text page and a scanned page. Other anchors include UTF-8 BOM
and Unicode character offsets, a DOCX table, an XLSX numeric cell, a hidden slide and quoted CSV.

```sh
uv run --locked --python 3.12.14 --no-default-groups --extra knowledge-base --extra knowledge-base-formats python -m evaluation.knowledge_base dry-run
uv run --locked --python 3.12.14 --no-default-groups --extra knowledge-base --extra knowledge-base-formats python -m evaluation.knowledge_base generate /tmp/kb-fixtures
uv run --locked --python 3.12.14 --no-default-groups --extra knowledge-base --extra knowledge-base-formats python -m evaluation.knowledge_base export-existing /tmp/kb-results
uv run --locked --python 3.12.14 --no-default-groups --extra knowledge-base --extra knowledge-base-formats python -m evaluation.knowledge_base parse /tmp/kb-results --subset
uv run --locked --python 3.12.14 --no-default-groups --extra knowledge-base --extra knowledge-base-formats python -m evaluation.knowledge_base parse /tmp/kb-results
uv run --locked --python 3.12.14 --no-default-groups --extra knowledge-base --extra knowledge-base-formats python -m evaluation.knowledge_base retrieve /tmp/kb-results --model-cache /path/to/offline/model-cache
```

Each later adapter writes `<fixture-name>.json` containing its real `ParsedDocument` to the results
directory. For the complete corpus, omit `--subset`. Parsing is scored per fixture with an exact
match after Unicode and whitespace normalization, complete ordered locator matches including expected
geometry anchors, and exact coverage counts and structured warnings. The scorer reports active profiles.
It checks exact installed profiles for existing adapters and format for the proposed adapters.
The export command reports wall time for existing adapters. The fixture set does not establish
maximum-size resource performance.

Retrieval embeds nonempty blocks and fixed queries separately with the pinned offline BGE model,
groups by source file and ranks files by their best block cosine similarity. The manifest records provisional policy targets.
Recall@3 ≥ 0.90 allows one miss over eleven independently labeled queries, and MRR@3 ≥ 0.70
requires at least 7.7 aggregate reciprocal-rank points. The targets remain provisional until #2856 runs the fixed model. The command reports scores without an acceptance
verdict. `dry-run` checks the metric formulas against hand-calculated cases. Fixed-model execution
is NOT_RUN for #2849 and belongs to #2856. The retrieval command verifies the five model files
before inference and requires all eleven results. This score measures ranking with fixed embeddings. Publication,
permissions and backend retrieval have their own acceptance tests.
[Embedding model revision](https://huggingface.co/Qdrant/bge-small-zh-v1.5/tree/46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59)

## Existing-format compatibility

Export the original parser from the Issue's reference commit into a separate directory, then
run the compatibility command against that directory with `--export` and against the current
checkout in comparison mode. The six samples cover both Markdown extensions and all four image
extensions. The outputs contain text, heading paths, source coordinates and chunk identities.

```sh
mkdir -p /tmp/kb-original
git archive e6e36a43b22240a827c9daec9798ccb256032608 plugins/knowledge-base | tar -x -C /tmp/kb-original
uv run --locked --python 3.12.14 --no-default-groups --extra knowledge-base --extra knowledge-base-formats \
  python -m evaluation.knowledge_base.legacy /tmp/kb-original --export /tmp/kb-original.json
cmp /tmp/kb-original.json evaluation/knowledge_base/legacy-baseline.json
uv run --locked --python 3.12.14 --no-default-groups --extra knowledge-base --extra knowledge-base-formats \
  python -m evaluation.knowledge_base.legacy .
```
