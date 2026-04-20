"""Tests for HTML rendering and the mailto builder."""

from __future__ import annotations

from ResolutionMessenger import config, recipients, templates


class TestMailto:
    def test_includes_marker_in_subject(self):
        url = templates.build_mailto(
            order_number="27341",
            customer="Joe Customer",
            delivery_date="2026-04-23",
            template_key=recipients.TEMPLATE_UNPAID,
            issue_summary="Delivery On Hold — Unpaid Balance",
        )
        # mailto: strings are URL-encoded. We check for the marker prefix in
        # both encoded and decoded forms so the test doesn't depend on
        # whether quote() encoded the colon.
        prefix = config.TRACKING_MARKER_PREFIX
        assert prefix in url or prefix in url.replace("%3A", ":")
        assert "27341" in url

    def test_includes_reply_to_and_cc(self):
        url = templates.build_mailto(
            "27341", "Customer", "2026-04-23",
            recipients.TEMPLATE_SUPPLY_WITH_PO, "issue",
        )
        assert url.startswith(f"mailto:{config.REPLY_TO_ADDRESS}")
        for cc in config.REPLY_CC_ADDRESSES:
            assert cc in url


class TestSubject:
    def test_standard_subject_has_warning_icon_and_order(self):
        subj = templates.subject_for(
            recipients.TEMPLATE_SUPPLY_WITH_PO,
            order_number="27341",
            is_final_warning=False,
        )
        assert "⚠️" in subj
        assert "27341" in subj

    def test_final_warning_subject_uses_siren(self):
        subj = templates.subject_for(
            recipients.TEMPLATE_SUPPLY_WITH_PO,
            order_number="27341",
            is_final_warning=True,
        )
        assert "🚨" in subj or "LAST WARNING" in subj
        assert "27341" in subj


class TestHtmlRender:
    def _render(self, **overrides):
        defaults = dict(
            template_key=recipients.TEMPLATE_SUPPLY_WITH_PO,
            order_number="27341",
            customer="Joe Customer",
            delivery_date="2026-04-23",
            days_before=3,
            threshold_label="T-3",
            po_numbers="12345",
            model_numbers="MODELX",
            issue_summary="Immediate Attention Needed",
            mailto_url="mailto:test@example.com",
            is_final_warning=False,
            last_comment=None,
        )
        defaults.update(overrides)
        return templates.render_html(**defaults)

    def test_includes_order_and_customer(self):
        html = self._render()
        assert "27341" in html
        assert "Joe Customer" in html

    def test_includes_mailto_url(self):
        html = self._render(mailto_url="mailto:wayne@example.com?subject=hi")
        assert "mailto:wayne@example.com" in html

    def test_final_warning_uses_black_banner(self):
        html = self._render(is_final_warning=True)
        # The final-warning banner uses #000000.
        assert "background-color:#000000" in html
        # And references the final-warning body text.
        assert "LAST WARNING" in html
        assert "15:00" in html or "3:00" in html.upper()

    def test_standard_uses_red_banner(self):
        html = self._render(is_final_warning=False)
        assert "#dc3545" in html

    def test_hidden_marker_present(self):
        html = self._render()
        # White-on-white 1px text — the marker is inside it.
        expected = f"{config.TRACKING_MARKER_PREFIX}:27341"
        assert expected in html
        assert "color:#ffffff;font-size:1px" in html

    def test_last_comment_is_rendered_when_provided(self):
        html = self._render(last_comment="Picking up from GE on Wednesday")
        assert "Picking up from GE on Wednesday" in html
        # "Last update" header
        assert "Last update" in html.lower() or "LAST UPDATE" in html.upper()

    def test_last_comment_absent_by_default(self):
        html = self._render()
        assert "Last update" not in html and "LAST UPDATE" not in html.upper()

    def test_model_and_po_rows_conditional(self):
        html_with = self._render(po_numbers="11111", model_numbers="MODELX")
        html_without = self._render(po_numbers=None, model_numbers=None)
        assert "11111" in html_with
        assert "MODELX" in html_with
        assert "11111" not in html_without
        assert "MODELX" not in html_without

    def test_html_escapes_dangerous_content(self):
        """A customer name with HTML special chars must be escaped."""
        html = self._render(customer="<script>alert(1)</script>")
        # Escaped form must be present; raw form must not.
        assert "&lt;script&gt;" in html
        assert "<script>alert(1)</script>" not in html
