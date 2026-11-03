#
# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Exercise bilingual publishing and the exported blog, alongside original site pages."""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEBSITE = ROOT / "website"


@pytest.fixture(scope="module")
def built_blog_site() -> None:
    required = [
        WEBSITE / "node_modules/.bin/tsx",
        WEBSITE / ".generated/blog.json",
        WEBSITE / "out/en/blog/index.html",
        WEBSITE / "out/zh/blog/index.html",
    ]
    if shutil.which("pnpm") and all(path.is_file() for path in required):
        return
    message = "Blog export tests require pnpm and a successful make docs-test build."
    if os.environ.get("POWERCONTEXT_TEST_BLOG_SITE") == "1":
        pytest.fail(message)
    pytest.skip(message)


@pytest.mark.usefixtures("built_blog_site")
def test_full_size_figure_links_require_exported_image_files() -> None:
    pnpm = shutil.which("pnpm")
    assert pnpm
    output = WEBSITE / "out"
    image = next((output / "docs-assets/blog").rglob("*.en.svg"))
    image_url = "/" + image.relative_to(output).as_posix()
    with TemporaryDirectory(prefix="figure-link-check-", dir=output) as directory:
        page = Path(directory) / "index.html"
        page.write_text(f'<a href="{image_url}">View full-size diagram</a>', encoding="utf-8")
        command = [pnpm, "exec", "tsx", "scripts/verify-static-export.ts"]
        subprocess.run(command, cwd=WEBSITE, check=True, capture_output=True, text=True)
        missing_url = image_url + ".missing.svg"
        page.write_text(f'<a href="{missing_url}">View full-size diagram</a>', encoding="utf-8")
        result = subprocess.run(command, cwd=WEBSITE, check=False, capture_output=True, text=True)
        assert result.returncode != 0
        assert "missing local assets" in result.stderr
        assert missing_url in result.stderr


@pytest.mark.usefixtures("built_blog_site")
def test_blog_i18n_pagination_and_publishing_contract() -> None:
    pnpm = shutil.which("pnpm")
    assert pnpm
    subprocess.run([pnpm, "exec", "tsx", "--test", "tests/blog.test.ts"], cwd=WEBSITE, check=True)


@pytest.mark.usefixtures("built_blog_site")
def test_exported_blog_and_original_pages() -> None:
    content = json.loads((WEBSITE / ".generated/blog.json").read_text(encoding="utf-8"))
    for language in ("en", "zh"):
        output = WEBSITE / "out" / language
        listing = (output / "blog/index.html").read_text(encoding="utf-8")
        assert f'<html lang="{language}"' in listing
        for post in content[language]["posts"]:
            article = (output / "blog" / post["slug"] / "index.html").read_text(encoding="utf-8")
            assert post["title"] in html.unescape(article)
            assert re.search(rf'href="/{language}/blog/{post["slug"]}/?"', listing)
            cover_url = "/docs-assets/" + post["cover"].removeprefix("assets/")
            assert f'src="{cover_url}"' in article
            assert f'src="{cover_url}"' in listing
            assert post["coverAlt"] in html.unescape(article)
            assert (WEBSITE / "out" / cover_url.lstrip("/")).is_file()
            assert 'property="og:image"' in article
            if post["slug"] == "building-durable-project-context":
                for number in range(1, 10):
                    original = f"/docs-assets/blog/{post['slug']}/figure-{number}.png"
                    assert f'src="{original}"' in article
                assert not re.search(r"building-durable-project-context/figure-\d+\.(?:en|zh)\.svg", article)
            for author_id in post["authors"]:
                author = content[language]["authors"][author_id]
                assert author["name"] in article
                profile = (output / "blog/authors" / author_id / "index.html").read_text(encoding="utf-8")
                assert author["description"] in profile
                assert author["name"] in profile
                for platform, handle in author["socials"].items():
                    domain = "github.com" if platform == "github" else "x.com"
                    for page in (article, profile):
                        assert f'href="https://{domain}/{handle}"' in page
                        assert f"@{handle}</span>" in re.sub(r"<!--.*?-->", "", page)
                assert "GitHub user:" not in article
                assert "GitHub 用户" not in article
        for route in ("index.html", "docs/index.html", "benchmarks/index.html", "changelog/index.html"):
            document = (output / route).read_text(encoding="utf-8")
            assert re.search(rf'href="/{language}/blog/?"', document)
            markup = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", document)
            assert "blog-module" not in markup
            for name in ("docs", "benchmarks", "changelog"):
                assert re.search(rf'href="/{language}/{name}/?"', markup)


def test_blog_language_is_consistent_in_text_examples_and_diagrams() -> None:
    """Catch untranslated prompts/comments and accidental cross-language image reuse."""
    original_images = {
        # Preserve the supplied MiniMax article artwork in both editions.
        *(f"building-durable-project-context/figure-{number}.png" for number in range(1, 10)),
        "deepseek-harness-powercontext/dsh-web.png",
        "deepseek-harness-powercontext/plugin-list.png",
        "deepseek-harness-powercontext/pc-command.png",
    }
    han = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
    for language in ("en", "zh"):
        for source in (ROOT / "docs" / language / "blog/posts").rglob("*.md"):
            markdown = source.read_text(encoding="utf-8")
            if language == "en":
                assert not han.search(markdown), f"Untranslated Chinese in {source}"
                assert "--language zh" not in markdown, source
            other = "zh" if language == "en" else "en"
            assert f"https://powercontext.oceanbase.io/{other}/" not in markdown, source
            for image in re.findall(r"!\[[^\]]*\]\(\.\./\.\./\.\./assets/blog/([^\s)]+)\)", markdown):
                if image in original_images:
                    assert (ROOT / "docs/assets/blog" / image).is_file()
                    continue
                assert image.endswith(f".{language}.svg"), f"Unlocalized diagram: {source}: {image}"
                asset = ROOT / "docs/assets/blog" / image
                svg = asset.read_text(encoding="utf-8")
                assert asset.with_name(asset.name.replace(f".{language}.svg", f".{other}.svg")).is_file()
                if language == "en":
                    assert not han.search(svg), f"Untranslated diagram text in {asset}"
