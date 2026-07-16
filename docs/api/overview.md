# API overview

The CLI is the shortest route for normal backups. The Python API exposes each
stage separately for applications that need custom storage, rendering, OCR, or
workflow orchestration.

| Module | Purpose |
| --- | --- |
| [`glyphive.archive`](archive.md) | Serialize a tree, list selected paths, and parse archive records |
| [`glyphive.codec`](codec.md) | Resolve named printable codecs and use `g1` |
| [`glyphive.compression`](compression.md) | Resolve whole-stream compression methods |
| [`glyphive.layout`](layout.md) | Paginate encoded lines and validate protected pages |
| [`glyphive.render`](render.md) | Resolve and invoke text, PDF, and Word renderers |
| [`glyphive.restore`](restore.md) | Decode a document and safely write archive records |
| [`glyphive.restore.ocr`](ocr.md) | Discover providers and OCR one or more images |

## Pipeline

```text
archive.archive_tree(root)
  -> compression.get(name).compress(raw)
  -> codec.get("g1").encode(payload)
  -> layout.paginate(lines, metadata, lines_per_page=...)
  -> render.render(pages, output, format)

restore.decode_document(text_lines)
  -> restore.unarchive_bytes(raw, destination)
```

Registry names are serialized in the protected document header. Treat them as
wire identifiers: adding an implementation under a new name is extensible;
changing the behavior of an existing identifier breaks compatibility.

Optional renderer and OCR dependencies are checked only when the corresponding
implementation is selected.
