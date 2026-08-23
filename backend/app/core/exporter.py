"""Renders the 10-deliverable package from the typed MigrationPlan/ArchitectureModel
— never re-generates content as fresh prose (technique #12, DECISIONS.md). PDF and
DOCX outputs are both pure functions of the canonical schemas.

Security note (flagged in the PRD review, addressed here): all user/LLM-derived text
that ends up in an export is sanitized first. DOCX text runs via python-docx and PDF
paragraphs via reportlab are both inserted as literal text (not interpreted as
markup), so injection risk is inherently low, but control characters are still
stripped for hygiene.
"""

from __future__ import annotations

import io
import re
from xml.sax.saxutils import escape

from docx import Document
from docx.shared import Pt
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.schemas.architecture import ArchitectureModel
from app.schemas.migration_context import MigrationContext
from app.schemas.migration_plan import MigrationPlan

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def sanitize_text(value: str) -> str:
    """Strips control characters. Safe baseline for any user/LLM-derived text
    before it's written into any export format."""

    return _CONTROL_CHARS_RE.sub("", value)


def render_pdf(model: ArchitectureModel, plan: MigrationPlan, context: MigrationContext | None) -> bytes:
    s = sanitize_text
    styles = getSampleStyleSheet()
    story: list = [Paragraph("Enterprise Architecture Migration Plan", styles["Title"]), Spacer(1, 0.2 * inch)]

    def heading(text: str) -> None:
        story.append(Paragraph(text, styles["Heading1"]))

    def subheading(text: str) -> None:
        story.append(Paragraph(text, styles["Heading2"]))

    def body(text: str) -> None:
        story.append(Paragraph(text, styles["BodyText"]))

    def bullets(items: list[str]) -> None:
        if items:
            story.append(
                ListFlowable([ListItem(Paragraph(item, styles["BodyText"])) for item in items], bulletType="bullet")
            )

    table_body_style = ParagraphStyle(
        "ExportTableBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=7,
        leading=8,
        wordWrap="CJK",
    )
    table_header_style = ParagraphStyle(
        "ExportTableHeader",
        parent=table_body_style,
        fontName="Helvetica-Bold",
        textColor=colors.black,
    )

    def table(headers: list[str], rows: list[list[str]], col_widths: list[float] | None = None) -> None:
        def cell(value: str, style: ParagraphStyle) -> Paragraph:
            return Paragraph(escape(sanitize_text(value)).replace("\n", "<br/>"), style)

        data = [[cell(header, table_header_style) for header in headers]]
        data.extend([[cell(value, table_body_style) for value in row] for row in rows])
        t = Table(data, colWidths=col_widths, hAlign="LEFT", repeatRows=1)
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(t)
        story.append(Spacer(1, 0.15 * inch))

    heading("1. Current Architecture")
    bullets(
        [
            f"<b>{s(c.name)}</b> ({c.id}) — {s(c.workload_type)}, {s(c.environment)}"
            f"{f', {s(c.technology)}' if c.technology else ''}"
            for c in model.components
        ]
    )
    if model.assumptions:
        subheading("Assumptions")
        bullets([s(a.text) for a in model.assumptions])

    heading("2. Target Architecture")
    body(s(plan.target_architecture_description))

    heading("3. Component Mapping")
    table(
        ["Component", "Disposition", "Target"],
        [[s(m.component_id), str(m.disposition), s(m.target_description)] for m in plan.component_mappings],
        col_widths=[1.25 * inch, 0.85 * inch, 5.05 * inch],
    )

    heading("4. Component Migration Approach")
    for p in plan.component_plans:
        subheading(f"{s(p.component_id)} (wave {p.wave_index}, {p.disposition})")
        bullets([s(step) for step in p.steps])
        if p.estimated_effort:
            body(f"Estimated effort: {s(p.estimated_effort)}")

    heading("5. Migration Sequence")
    for w in plan.waves:
        body(f"<b>Wave {w.index}</b>: {', '.join(s(c) for c in w.component_ids)} — {s(w.rationale)}")
        for g in w.coexistence_groups:
            body(f"Coexistence ({', '.join(s(c) for c in g.component_ids)}): {s(g.coexistence_strategy)}")

    heading("6. Risks & Assumptions")
    bullets([f"[{r.severity}] {s(r.description)} — mitigation: {s(r.mitigation)}" for r in plan.risks])

    heading("7. Validation Approach")
    if plan.validation_summary:
        body(s(plan.validation_summary.overall_strategy))
        bullets(
            [f"{s(check.check_type)}: {s(check.description)}" for check in plan.validation_summary.cross_component_checks]
        )

    heading("8. Cutover Strategy")
    if plan.cutover_strategy:
        body(s(plan.cutover_strategy.approach))
        bullets([s(step) for step in plan.cutover_strategy.steps])
        subheading("Go/No-Go criteria")
        bullets([s(c) for c in plan.cutover_strategy.go_no_go_criteria])

    heading("9. Rollback Strategy")
    if plan.rollback_strategy:
        body(s(plan.rollback_strategy.approach))
        bullets([s(step) for step in plan.rollback_strategy.steps])

    heading("10. Migration Roadmap")
    table(
        ["Wave", "Component", "Disposition", "Summary", "Owner", "Effort", "Depends on waves"],
        [
            [
                str(item.wave_index),
                s(item.component_id),
                str(item.disposition),
                s(item.summary),
                s(item.owner_placeholder),
                s(item.estimated_effort or "-"),
                ", ".join(str(w) for w in item.depends_on_waves) or "-",
            ]
            for item in plan.roadmap_items
        ],
        col_widths=[0.4 * inch, 1.1 * inch, 0.75 * inch, 2.25 * inch, 0.75 * inch, 1.0 * inch, 0.9 * inch],
    )

    if context:
        heading("Migration Context")
        body(f"Source: {context.source_environment} &#8594; Target: {context.target_environment}")
        body(f"Target platform: {s(context.target_platform_description)}")
        body(f"Downtime tolerance: {context.downtime_tolerance}")
        if context.maintenance_window_description:
            body(f"Maintenance window: {s(context.maintenance_window_description)}")
        if context.target_completion_description:
            body(f"Target completion: {s(context.target_completion_description)}")
        bullets([f"Constraint: {s(constraint)}" for constraint in context.constraints])

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=LETTER, topMargin=0.75 * inch, bottomMargin=0.75 * inch).build(story)
    return buffer.getvalue()


def render_docx(model: ArchitectureModel, plan: MigrationPlan, context: MigrationContext | None) -> bytes:
    s = sanitize_text
    doc = Document()
    doc.styles["Normal"].font.size = Pt(10.5)

    doc.add_heading("Enterprise Architecture Migration Plan", level=0)

    doc.add_heading("1. Current Architecture", level=1)
    for c in model.components:
        doc.add_paragraph(
            f"{s(c.name)} ({c.id}) — {s(c.workload_type)}, {s(c.environment)}"
            f"{f', {s(c.technology)}' if c.technology else ''}",
            style="List Bullet",
        )

    doc.add_heading("2. Target Architecture", level=1)
    doc.add_paragraph(s(plan.target_architecture_description))

    doc.add_heading("3. Component Mapping", level=1)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text = "Component", "Disposition", "Target"
    for m in plan.component_mappings:
        row = table.add_row().cells
        row[0].text, row[1].text, row[2].text = s(m.component_id), str(m.disposition), s(m.target_description)

    doc.add_heading("4. Component Migration Approach", level=1)
    for p in plan.component_plans:
        doc.add_heading(f"{s(p.component_id)} (wave {p.wave_index}, {p.disposition})", level=2)
        for step in p.steps:
            doc.add_paragraph(s(step), style="List Number")

    doc.add_heading("5. Migration Sequence", level=1)
    for w in plan.waves:
        doc.add_paragraph(f"Wave {w.index}: {', '.join(s(c) for c in w.component_ids)} — {s(w.rationale)}",
                           style="List Bullet")
        for g in w.coexistence_groups:
            doc.add_paragraph(
                f"Coexistence ({', '.join(s(c) for c in g.component_ids)}): {s(g.coexistence_strategy)}",
                style="List Bullet 2",
            )

    doc.add_heading("6. Risks & Assumptions", level=1)
    for r in plan.risks:
        doc.add_paragraph(f"[{r.severity}] {s(r.description)} — mitigation: {s(r.mitigation)}", style="List Bullet")

    doc.add_heading("7. Validation Approach", level=1)
    if plan.validation_summary:
        doc.add_paragraph(s(plan.validation_summary.overall_strategy))
        for check in plan.validation_summary.cross_component_checks:
            doc.add_paragraph(f"{s(check.check_type)}: {s(check.description)}", style="List Bullet")

    doc.add_heading("8. Cutover Strategy", level=1)
    if plan.cutover_strategy:
        doc.add_paragraph(s(plan.cutover_strategy.approach))
        for step in plan.cutover_strategy.steps:
            doc.add_paragraph(s(step), style="List Number")

    doc.add_heading("9. Rollback Strategy", level=1)
    if plan.rollback_strategy:
        doc.add_paragraph(s(plan.rollback_strategy.approach))
        for step in plan.rollback_strategy.steps:
            doc.add_paragraph(s(step), style="List Number")

    doc.add_heading("10. Migration Roadmap", level=1)
    table = doc.add_table(rows=1, cols=6)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    for i, title in enumerate(["Wave", "Component", "Disposition", "Summary", "Owner", "Effort"]):
        hdr[i].text = title
    for item in plan.roadmap_items:
        row = table.add_row().cells
        row[0].text = str(item.wave_index)
        row[1].text = s(item.component_id)
        row[2].text = str(item.disposition)
        row[3].text = s(item.summary)
        row[4].text = s(item.owner_placeholder)
        row[5].text = s(item.estimated_effort or "-")

    if context:
        doc.add_heading("Migration Context", level=1)
        doc.add_paragraph(f"Source: {context.source_environment} -> Target: {context.target_environment}")
        doc.add_paragraph(f"Target platform: {s(context.target_platform_description)}")
        doc.add_paragraph(f"Downtime tolerance: {context.downtime_tolerance}")
        if context.maintenance_window_description:
            doc.add_paragraph(f"Maintenance window: {s(context.maintenance_window_description)}")
        if context.target_completion_description:
            doc.add_paragraph(f"Target completion: {s(context.target_completion_description)}")
        for constraint in context.constraints:
            doc.add_paragraph(s(constraint), style="List Bullet")

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
