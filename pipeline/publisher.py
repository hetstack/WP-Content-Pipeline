"""M7: Publisher — publikacja na WordPress jako DRAFT."""

import os
import json
import logging
import markdown
from .database import get_db
from .wp_session import WordPressSession

log = logging.getLogger(__name__)


class Publisher:
    def __init__(self, **kwargs):
        wp_url = os.environ.get("WP_URL", "")
        wp_user = os.environ.get("WP_USER", "")
        wp_pass = os.environ.get("WP_APP_PASSWORD", "")

        self.wp = WordPressSession(wp_url)
        self.wp.set_credentials(wp_user, wp_pass)
        self.wp_external_url = os.environ.get("WP_EXTERNAL_URL", wp_url)

    def publish(self, article_id: int = None) -> dict:
        """Opublikuj artykuł jako draft na WordPress."""
        db = get_db()

        # Znajdź artykuł
        if article_id:
            article = db.execute(
                "SELECT * FROM articles WHERE id = ?", (article_id,)
            ).fetchone()
        else:
            article = db.execute(
                """SELECT * FROM articles
                   WHERE status IN ('translated', 'reviewed')
                     AND (wp_post_id IS NULL OR wp_post_id = 0)
                   ORDER BY created_at DESC LIMIT 1"""
            ).fetchone()

        if not article:
            # Fallback: dowolny artykuł z treścią PL
            article = db.execute(
                """SELECT * FROM articles
                   WHERE content_pl IS NOT NULL AND content_pl != ''
                   ORDER BY created_at DESC LIMIT 1"""
            ).fetchone()

        if not article:
            return {"event": "error", "message": "Brak artykułu do publikacji"}

        # Konwertuj article na dict 
        article = dict(article)

        content_pl = article.get("content_pl") or article.get("content_en_rev") or article.get("content_en")
        title = article.get("title_pl") or article.get("title_en")

        if not content_pl:
            return {"event": "error", "message": "Artykuł nie ma treści"}

        # Markdown → HTML
        try:
            html_content = markdown.markdown(
                content_pl,
                extensions=["fenced_code", "tables", "nl2br"]
            )
        except Exception as e:
            return {"event": "error", "message": f"Konwersja Markdown failed: {e}"}

        # Kategoria
        wp_category = article.get("wp_category", "DevOps")

        # Pobierz z briefu jeśli brak w artykule
        if not wp_category or wp_category == "DevOps":
            brief = db.execute(
                "SELECT wp_category, wp_tags FROM briefs WHERE id = ?",
                (article.get("brief_id"),)
            ).fetchone()
            if brief:
                brief = dict(brief)
                wp_category = brief["wp_category"] or wp_category

        category_id = self._get_or_create_category(wp_category)

        # Tagi
        wp_tags_raw = article.get("wp_tags", "[]")
        try:
            wp_tags = json.loads(wp_tags_raw) if isinstance(wp_tags_raw, str) else wp_tags_raw or []
        except (json.JSONDecodeError, TypeError):
            wp_tags = []

        # Pobierz tagi z briefu jeśli brak
        if not wp_tags and article.get("brief_id"):
            brief = db.execute(
                "SELECT wp_tags FROM briefs WHERE id = ?",
                (article.get("brief_id"),)
            ).fetchone()
            if brief:
                brief = dict(brief)
                if brief["wp_tags"]:
                    try:
                        wp_tags = json.loads(brief["wp_tags"])
                    except (json.JSONDecodeError, TypeError):
                        wp_tags = []

        tag_ids = [self._get_or_create_tag(t) for t in wp_tags]
        tag_ids = [t for t in tag_ids if t]

        # Tworzenie posta
        post_data = {
            "title": title,
            "content": html_content,
            "excerpt": article.get("excerpt_pl", ""),
            "status": "draft",
        }
        if category_id:
            post_data["categories"] = [category_id]
        if tag_ids:
            post_data["tags"] = tag_ids

        try:
            r = self.wp.post("/?rest_route=/wp/v2/posts", json=post_data)

            if r.status_code in (200, 201):
                post = r.json()
                wp_post_id = post["id"]
                wp_post_url = post.get("link", "")

                # Zamień internal URL na external
                if self.wp_external_url and self.wp.base_url != self.wp_external_url:
                    wp_post_url = wp_post_url.replace(
                        self.wp.base_url, self.wp_external_url
                    )

                # Zapisz w bazie
                db.execute(
                    """UPDATE articles
                       SET wp_post_id = ?, wp_post_url = ?, status = 'published'
                       WHERE id = ?""",
                    (wp_post_id, wp_post_url, article["id"])
                )
                if article.get("brief_id"):
                    db.execute(
                        "UPDATE briefs SET status = 'done' WHERE id = ?",
                        (article.get("brief_id"),)
                    )
                db.commit()

                log.info(f"Published post #{wp_post_id}: {title}")
                return {
                    "event": "published",
                    "wp_post_id": wp_post_id,
                    "wp_post_url": wp_post_url,
                    "title": title,
                    "category": wp_category,
                    "tags": wp_tags
                }
            else:
                error_msg = f"WordPress HTTP {r.status_code}: {r.text[:300]}"
                log.error(error_msg)
                return {"event": "error", "message": error_msg}

        except Exception as e:
            log.error(f"WordPress publish failed: {e}")
            return {"event": "error", "message": f"WordPress publish failed: {e}"}

    def _get_or_create_category(self, name: str) -> int | None:
        try:
            r = self.wp.get("/?rest_route=/wp/v2/categories",
                           params={"search": name, "per_page": 5})
            if r.status_code == 200:
                for cat in r.json():
                    if cat["name"].lower() == name.lower():
                        return cat["id"]

            r = self.wp.post("/?rest_route=/wp/v2/categories", json={"name": name})
            if r.status_code in (200, 201):
                return r.json().get("id")
        except Exception as e:
            log.warning(f"Category error {name}: {e}")
        return None

    def _get_or_create_tag(self, name: str) -> int | None:
        try:
            r = self.wp.get("/?rest_route=/wp/v2/tags",
                           params={"search": name, "per_page": 5})
            if r.status_code == 200:
                for tag in r.json():
                    if tag["name"].lower() == name.lower():
                        return tag["id"]

            r = self.wp.post("/?rest_route=/wp/v2/tags", json={"name": name})
            if r.status_code in (200, 201):
                return r.json().get("id")
        except Exception as e:
            log.warning(f"Tag error {name}: {e}")
        return None
