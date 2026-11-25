import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from podcaster import pipeline, sources

HTML = "<html><head><title>T</title></head><body><nav>menu menu</nav><article><p>The article body says one thing plainly and at some length so the extractor keeps it.</p><p>A second paragraph follows with more of the same.</p></article></body></html>"


def two_page_pdf(path: Path) -> Path:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    w = PdfWriter()
    for text in ("first page words", "second page words"):
        page = w.add_blank_page(width=200, height=200)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = w._add_object(stream)
    with open(path, "wb") as fh:
        w.write(fh)
    return path


class ReadSources(unittest.TestCase):
    def test_pdf_pages_are_marked(self):
        with tempfile.TemporaryDirectory() as d:
            two_page_pdf(Path(d) / "paper.pdf")
            text = sources.read_sources(Path(d))
        self.assertIn("# source: paper.pdf", text)
        self.assertIn("# page 1\n\nfirst page words", text)
        self.assertIn("# page 2\n\nsecond page words", text)

    def test_html_keeps_the_article_and_drops_the_nav(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "page.html").write_text(HTML)
            text = sources.read_sources(Path(d))
        self.assertIn("article body", text)
        self.assertNotIn("menu menu", text)

    def test_url_files_are_skipped_without_fetch_and_read_with_it(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "links.url").write_text("# comment\nhttps://example.test/a\n")
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(sources.read_sources(Path(d)), "")
            self.assertIn("pass --fetch", err.getvalue())
            text = sources.read_sources(Path(d), fetch=lambda url: HTML)
        self.assertIn("# url: https://example.test/a", text)
        self.assertIn("article body", text)

    def test_failed_fetch_is_noted_not_fatal(self):
        def boom(url: str) -> str:
            raise OSError("down")

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "links.url").write_text("https://example.test/a\n")
            (Path(d) / "note.md").write_text("kept\n")
            err = io.StringIO()
            with redirect_stderr(err):
                text = sources.read_sources(Path(d), fetch=boom)
        self.assertIn("kept", text)
        self.assertIn("skipped https://example.test/a: down", err.getvalue())

    def test_cap_truncates_with_a_marker(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "big.txt").write_text("w " * 500)
            err = io.StringIO()
            with redirect_stderr(err):
                text = sources.read_sources(Path(d), max_words=100)
        self.assertIn("# truncated:", text)
        self.assertIn("cut to 100", err.getvalue())
        self.assertLess(len(text.split()), 120)

    def test_text_files_still_concatenate_in_order(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "b.txt").write_text("second\n")
            (Path(d) / "a.md").write_text("first\n")
            (Path(d) / "skip.docx").write_bytes(b"x")
            self.assertEqual(sources.read_sources(Path(d)), "# source: a.md\n\nfirst\n\n# source: b.txt\n\nsecond\n")


class FetchGuards(unittest.TestCase):
    """--fetch is the only outbound path in the repo, and what comes back enters the prompt."""

    def test_only_http_and_https_are_fetched(self):
        for url in ("file:///etc/passwd", "gopher://x/1", "ftp://example.com/a"):
            with self.assertRaises(OSError) as ctx:
                sources.check_url(url)
            self.assertIn("only http and https", str(ctx.exception))

    def test_loopback_is_refused_by_name_and_by_address(self):
        for url in ("http://127.0.0.1:8080/admin", "http://localhost:37701/search", "http://[::1]/x"):
            with self.assertRaises(OSError) as ctx:
                sources.check_url(url)
            self.assertIn("private address", str(ctx.exception))

    def test_private_and_link_local_ranges_are_refused(self):
        for url in ("http://10.0.0.5/x", "http://192.168.1.1/x", "http://169.254.169.254/latest/meta-data/"):
            with self.assertRaises(OSError):
                sources.check_url(url)

    def test_a_url_with_no_host_is_refused(self):
        with self.assertRaises(OSError):
            sources.check_url("http:///nohost")


class Symlinks(unittest.TestCase):
    def test_a_symlinked_source_is_not_read(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            secret = root / "secret.txt"
            secret.write_text("a private key would be here\n")
            (root / "real.md").write_text("real material\n")
            (root / "notes.md").symlink_to(secret)
            self.assertFalse(sources.readable_file(root / "notes.md"))
            names = [p.name for p in sources.source_files(root)]
        self.assertEqual(names, ["real.md", "secret.txt"])  # the link itself is gone

    def test_resolve_sources_skips_a_symlink_in_a_folder(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            outside = Path(d) / "outside.md"
            outside.write_text("x\n")
            inner = root / "folder"
            inner.mkdir()
            (inner / "real.md").write_text("y\n")
            (inner / "link.md").symlink_to(outside)
            names = [p.name for p in pipeline.resolve_sources([inner])]
        self.assertEqual(names, ["real.md"])


class ThroughTheWriterInput(unittest.TestCase):
    """The readers sit behind build_writer_input, so the marker contract holds for every format.

    sources.py turns each file into text; build_writer_input marks each source so the
    grounding checker can tell them apart. These tests hold the two together.
    """

    def test_a_pdf_arrives_under_its_own_marker_with_page_markers_intact(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            two_page_pdf(root / "paper.pdf")
            (root / "notes.md").write_text("plain notes\n")
            text = pipeline.build_writer_input(pipeline.resolve_sources([root]))
        self.assertIn("<<< source: notes.md >>>", text)
        self.assertIn("<<< source: paper.pdf >>>", text)
        self.assertIn("# page 1", text)
        self.assertIn("first page words", text)
        # Order follows resolve_sources, which sorts a folder by name.
        self.assertLess(text.index("notes.md"), text.index("paper.pdf"))

    def test_html_is_extracted_not_passed_through_raw(self):
        with tempfile.TemporaryDirectory() as d:
            page = Path(d) / "page.html"
            page.write_text(HTML)
            text = pipeline.build_writer_input([page])
        self.assertIn("The article body says one thing plainly", text)
        self.assertNotIn("<article>", text)

    def test_a_url_file_is_not_fetched_without_fetch(self):
        with tempfile.TemporaryDirectory() as d:
            link = Path(d) / "links.url"
            link.write_text("https://example.invalid/a\n")
            err = io.StringIO()
            with redirect_stderr(err):
                text = pipeline.build_writer_input([link])
        self.assertIn("<<< source: links.url >>>", text)
        self.assertIn("pass --fetch", err.getvalue())

    def test_the_word_cap_marks_the_cut(self):
        with tempfile.TemporaryDirectory() as d:
            big = Path(d) / "big.md"
            big.write_text("word " * 300)
            err = io.StringIO()
            with redirect_stderr(err):
                text = pipeline.build_writer_input([big], max_words=100)
        self.assertIn("# truncated:", text)
        self.assertIn("cut to 100", err.getvalue())

    def test_truncation_keeps_every_marker_on_its_own_line(self):
        # Joining the input and slicing words collapses it onto one line, markers included,
        # so past the cap the grounding checker finds no markers and scores every claim
        # against undifferentiated text.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name in ("a.md", "b.md"):
                (root / name).write_text("word " * 200)
            err = io.StringIO()
            with redirect_stderr(err):
                text = pipeline.build_writer_input(pipeline.resolve_sources([root]), max_words=150)
        markers = [ln for ln in text.splitlines() if ln.startswith("<<< source:")]
        self.assertEqual(markers, ["<<< source: a.md >>>"])
        self.assertIn("1 source(s) cut entirely", text)

    def test_a_whole_source_past_the_cap_is_named_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name in ("a.md", "b.md", "c.md"):
                (root / name).write_text("word " * 100)
            err = io.StringIO()
            with redirect_stderr(err):
                pipeline.build_writer_input(pipeline.resolve_sources([root]), max_words=100)
        self.assertIn("2 source(s) cut entirely", err.getvalue())

    def test_no_cap_is_the_default_so_a_caller_opts_in(self):
        with tempfile.TemporaryDirectory() as d:
            big = Path(d) / "big.md"
            big.write_text("word " * 300)
            self.assertNotIn("# truncated:", pipeline.build_writer_input([big]))


if __name__ == "__main__":
    unittest.main()
