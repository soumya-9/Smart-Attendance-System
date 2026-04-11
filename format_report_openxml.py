import os
import re
import shutil
import tempfile
import zipfile
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET


DOC_PATH = Path(r"C:\Users\soumy\Downloads\SMART ATTENDANCE SYSTEM.docx")

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML_NS = "http://www.w3.org/XML/1998/namespace"

NS = {"w": W_NS, "r": R_NS}

ET.register_namespace("w", W_NS)
ET.register_namespace("r", R_NS)
ET.register_namespace("xml", XML_NS)


def qn(prefix_colon_tag: str) -> str:
    prefix, tag = prefix_colon_tag.split(":")
    return f"{{{NS[prefix]}}}{tag}"


def normalize_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace("\r", " ").replace("\n", " ").strip())


def paragraph_text(p) -> str:
    return normalize_text("".join(t.text or "" for t in p.findall(".//w:t", NS)))


def has_ancestor(elem, tag_local: str, parent_map) -> bool:
    target = qn(f"w:{tag_local}")
    current = parent_map.get(elem)
    while current is not None:
        if current.tag == target:
            return True
        current = parent_map.get(current)
    return False


def get_or_create(parent, tag):
    child = parent.find(tag, NS)
    if child is None:
        child = ET.Element(qn(tag))
        parent.append(child)
    return child


def remove_children(parent, tags):
    for child in list(parent):
        if child.tag in tags:
            parent.remove(child)


def set_style_format(style_root, style_id, font_size_half_points, bold=False, align="both", before=0, after=120, line=360):
    style = None
    for candidate in style_root.findall("w:style", NS):
        if candidate.get(qn("w:styleId")) == style_id:
            style = candidate
            break
    if style is None:
        return

    ppr = get_or_create(style, "w:pPr")
    remove_children(ppr, {qn("w:spacing"), qn("w:jc")})
    ET.SubElement(
        ppr,
        qn("w:spacing"),
        {
            qn("w:before"): str(before),
            qn("w:after"): str(after),
            qn("w:line"): str(line),
            qn("w:lineRule"): "auto",
        },
    )
    ET.SubElement(ppr, qn("w:jc"), {qn("w:val"): align})

    rpr = get_or_create(style, "w:rPr")
    remove_children(
        rpr,
        {
            qn("w:rFonts"),
            qn("w:sz"),
            qn("w:szCs"),
            qn("w:b"),
            qn("w:bCs"),
        },
    )
    ET.SubElement(
        rpr,
        qn("w:rFonts"),
        {
            qn("w:ascii"): "Times New Roman",
            qn("w:hAnsi"): "Times New Roman",
            qn("w:eastAsia"): "Times New Roman",
            qn("w:cs"): "Times New Roman",
        },
    )
    ET.SubElement(rpr, qn("w:sz"), {qn("w:val"): str(font_size_half_points)})
    ET.SubElement(rpr, qn("w:szCs"), {qn("w:val"): str(font_size_half_points)})
    if bold:
        ET.SubElement(rpr, qn("w:b"))
        ET.SubElement(rpr, qn("w:bCs"))


def set_para_style(p, style_id):
    ppr = get_or_create(p, "w:pPr")
    pstyle = ppr.find("w:pStyle", NS)
    if pstyle is None:
        pstyle = ET.Element(qn("w:pStyle"))
        ppr.insert(0, pstyle)
    pstyle.set(qn("w:val"), style_id)
    return ppr


def set_para_layout(p, align="both", before=0, after=120, line=360):
    ppr = get_or_create(p, "w:pPr")
    remove_children(ppr, {qn("w:spacing"), qn("w:jc")})
    ET.SubElement(
        ppr,
        qn("w:spacing"),
        {
            qn("w:before"): str(before),
            qn("w:after"): str(after),
            qn("w:line"): str(line),
            qn("w:lineRule"): "auto",
        },
    )
    ET.SubElement(ppr, qn("w:jc"), {qn("w:val"): align})
    return ppr


def set_numpr(p, num_id="16", ilvl="0"):
    ppr = get_or_create(p, "w:pPr")
    existing = ppr.find("w:numPr", NS)
    if existing is not None:
        ppr.remove(existing)
    numpr = ET.SubElement(ppr, qn("w:numPr"))
    ET.SubElement(numpr, qn("w:ilvl"), {qn("w:val"): ilvl})
    ET.SubElement(numpr, qn("w:numId"), {qn("w:val"): num_id})


def remove_numpr(p):
    ppr = p.find("w:pPr", NS)
    if ppr is None:
        return
    numpr = ppr.find("w:numPr", NS)
    if numpr is not None:
        ppr.remove(numpr)


def set_run_format_on_paragraph(p, font_size_half_points=24, bold=False):
    for r in p.findall("w:r", NS):
        rpr = get_or_create(r, "w:rPr")
        remove_children(
            rpr,
            {
                qn("w:rFonts"),
                qn("w:sz"),
                qn("w:szCs"),
                qn("w:b"),
                qn("w:bCs"),
            },
        )
        ET.SubElement(
            rpr,
            qn("w:rFonts"),
            {
                qn("w:ascii"): "Times New Roman",
                qn("w:hAnsi"): "Times New Roman",
                qn("w:eastAsia"): "Times New Roman",
                qn("w:cs"): "Times New Roman",
            },
        )
        ET.SubElement(rpr, qn("w:sz"), {qn("w:val"): str(font_size_half_points)})
        ET.SubElement(rpr, qn("w:szCs"), {qn("w:val"): str(font_size_half_points)})
        if bold:
            ET.SubElement(rpr, qn("w:b"))
            ET.SubElement(rpr, qn("w:bCs"))


def replace_paragraph_text(p, new_text):
    ppr = p.find("w:pPr", NS)
    for child in list(p):
        if child is not ppr:
            p.remove(child)
    run = ET.SubElement(p, qn("w:r"))
    rpr = ET.SubElement(run, qn("w:rPr"))
    ET.SubElement(
        rpr,
        qn("w:rFonts"),
        {
            qn("w:ascii"): "Times New Roman",
            qn("w:hAnsi"): "Times New Roman",
            qn("w:eastAsia"): "Times New Roman",
            qn("w:cs"): "Times New Roman",
        },
    )
    ET.SubElement(rpr, qn("w:sz"), {qn("w:val"): "24"})
    ET.SubElement(rpr, qn("w:szCs"), {qn("w:val"): "24"})
    t = ET.SubElement(run, qn("w:t"))
    if new_text.startswith(" ") or new_text.endswith(" "):
        t.set(f"{{{XML_NS}}}space", "preserve")
    t.text = new_text


def create_toc_field_paragraph():
    p = ET.Element(qn("w:p"))
    set_para_style(p, "Normal")
    set_para_layout(p, align="both", before=0, after=120, line=360)

    r1 = ET.SubElement(p, qn("w:r"))
    fld_begin = ET.SubElement(r1, qn("w:fldChar"))
    fld_begin.set(qn("w:fldCharType"), "begin")

    r2 = ET.SubElement(p, qn("w:r"))
    instr = ET.SubElement(r2, qn("w:instrText"))
    instr.set(f"{{{XML_NS}}}space", "preserve")
    instr.text = ' TOC \\o "1-3" \\h \\z \\u '

    r3 = ET.SubElement(p, qn("w:r"))
    fld_sep = ET.SubElement(r3, qn("w:fldChar"))
    fld_sep.set(qn("w:fldCharType"), "separate")

    r4 = ET.SubElement(p, qn("w:r"))
    t = ET.SubElement(r4, qn("w:t"))
    t.text = "Table of Contents"

    r5 = ET.SubElement(p, qn("w:r"))
    fld_end = ET.SubElement(r5, qn("w:fldChar"))
    fld_end.set(qn("w:fldCharType"), "end")

    set_run_format_on_paragraph(p, font_size_half_points=24, bold=False)
    return p


def create_footer_page_number():
    root = ET.Element(qn("w:ftr"))
    p = ET.SubElement(root, qn("w:p"))
    set_para_layout(p, align="center", before=0, after=0, line=240)

    r1 = ET.SubElement(p, qn("w:r"))
    r1pr = ET.SubElement(r1, qn("w:rPr"))
    ET.SubElement(
        r1pr,
        qn("w:rFonts"),
        {
            qn("w:ascii"): "Times New Roman",
            qn("w:hAnsi"): "Times New Roman",
            qn("w:eastAsia"): "Times New Roman",
            qn("w:cs"): "Times New Roman",
        },
    )
    ET.SubElement(r1pr, qn("w:sz"), {qn("w:val"): "24"})
    ET.SubElement(r1pr, qn("w:szCs"), {qn("w:val"): "24"})
    fld_begin = ET.SubElement(r1, qn("w:fldChar"))
    fld_begin.set(qn("w:fldCharType"), "begin")

    r2 = ET.SubElement(p, qn("w:r"))
    r2pr = deepcopy(r1pr)
    r2.append(r2pr)
    instr = ET.SubElement(r2, qn("w:instrText"))
    instr.set(f"{{{XML_NS}}}space", "preserve")
    instr.text = " PAGE "

    r3 = ET.SubElement(p, qn("w:r"))
    r3pr = deepcopy(r1pr)
    r3.append(r3pr)
    fld_sep = ET.SubElement(r3, qn("w:fldChar"))
    fld_sep.set(qn("w:fldCharType"), "separate")

    r4 = ET.SubElement(p, qn("w:r"))
    r4pr = deepcopy(r1pr)
    r4.append(r4pr)
    t = ET.SubElement(r4, qn("w:t"))
    t.text = "1"

    r5 = ET.SubElement(p, qn("w:r"))
    r5pr = deepcopy(r1pr)
    r5.append(r5pr)
    fld_end = ET.SubElement(r5, qn("w:fldChar"))
    fld_end.set(qn("w:fldCharType"), "end")

    return root


def get_level(text: str) -> int:
    if re.match(r"^\d+\.\d+\.\d+\s+", text):
        return 3
    if re.match(r"^\d+\.\d+\s+", text):
        return 2
    if text == "INTRODUCTION":
        return 1
    if re.match(r"^\d+\.\s+", text):
        return 1
    return 0


def is_short_list_item(text: str) -> bool:
    if not text or len(text) > 120:
        return False
    if re.match(r"^\d", text):
        return False
    if re.search(r"[\.:]$", text):
        return False
    if re.match(r"^(Figure|Table)\s*\d+", text):
        return False
    if get_level(text):
        return False
    return True


def main():
    if not DOC_PATH.exists():
        raise FileNotFoundError(DOC_PATH)

    backup_path = DOC_PATH.with_name(f"{DOC_PATH.stem} - backup{DOC_PATH.suffix}")
    shutil.copy2(DOC_PATH, backup_path)

    temp_dir = Path(tempfile.mkdtemp(prefix="docx_format_"))
    try:
        with zipfile.ZipFile(DOC_PATH, "r") as zin:
            zin.extractall(temp_dir)

        document_xml = temp_dir / "word" / "document.xml"
        styles_xml = temp_dir / "word" / "styles.xml"
        settings_xml = temp_dir / "word" / "settings.xml"
        footer_xml = temp_dir / "word" / "footer1.xml"

        doc_tree = ET.parse(document_xml)
        doc_root = doc_tree.getroot()
        body = doc_root.find("w:body", NS)

        style_tree = ET.parse(styles_xml)
        style_root = style_tree.getroot()

        settings_tree = ET.parse(settings_xml)
        settings_root = settings_tree.getroot()

        set_style_format(style_root, "Normal", 24, bold=False, align="both", before=0, after=120, line=360)
        set_style_format(style_root, "Heading1", 28, bold=True, align="left", before=240, after=240, line=240)
        set_style_format(style_root, "Heading2", 24, bold=True, align="left", before=240, after=240, line=240)
        set_style_format(style_root, "Heading3", 24, bold=True, align="left", before=240, after=240, line=240)
        set_style_format(style_root, "TOCHeading", 28, bold=True, align="left", before=240, after=240, line=240)
        set_style_format(style_root, "Caption", 24, bold=False, align="center", before=120, after=120, line=240)

        top_level_paragraphs = []
        for idx, child in enumerate(list(body)):
            if child.tag == qn("w:p"):
                top_level_paragraphs.append((idx, child, paragraph_text(child)))

        toc_pos = lof_pos = None
        approval_pos = None
        for idx, p, text in top_level_paragraphs:
            if approval_pos is None and text == "Approval Certificate":
                approval_pos = idx
            if toc_pos is None and text in {"Table of Contents", "TABLE OF CONTENTS"}:
                toc_pos = idx
            if lof_pos is None and text in {"List of Figures", "LIST OF FIGURES"}:
                lof_pos = idx

        if toc_pos is not None and lof_pos is not None and lof_pos > toc_pos:
            toc_para = list(body)[toc_pos]
            replace_paragraph_text(toc_para, "TABLE OF CONTENTS")
            set_para_style(toc_para, "TOCHeading")
            set_para_layout(toc_para, align="left", before=240, after=240, line=240)
            set_run_format_on_paragraph(toc_para, font_size_half_points=28, bold=True)

            for remove_index in range(lof_pos - 1, toc_pos, -1):
                body.remove(list(body)[remove_index])
            insert_at = toc_pos + 1
            body.insert(insert_at, create_toc_field_paragraph())

        parent_map = {child: parent for parent in doc_root.iter() for child in parent}
        paragraphs = [p for p in body.iterfind(".//w:p", NS)]

        started_formatting = False
        body_started = False
        chapter_counter = 0
        list_mode = False

        front_headings = {
            "Approval Certificate": "APPROVAL CERTIFICATE",
            "APPROVAL CERTIFICATE": "APPROVAL CERTIFICATE",
            "ACKNOWLEDGEMENT": "ACKNOWLEDGEMENT",
            "ABSTRACT": "ABSTRACT",
            "TABLE OF CONTENTS": "TABLE OF CONTENTS",
            "Table of Contents": "TABLE OF CONTENTS",
            "List of Figures": "LIST OF FIGURES",
            "LIST OF FIGURES": "LIST OF FIGURES",
            "List of Tables": "LIST OF TABLES",
            "LIST OF TABLES": "LIST OF TABLES",
        }

        for p in paragraphs:
            if has_ancestor(p, "tbl", parent_map):
                continue

            text = paragraph_text(p)
            if not started_formatting:
                if text in {"Approval Certificate", "APPROVAL CERTIFICATE"}:
                    started_formatting = True
                else:
                    continue

            if not text:
                list_mode = False
                continue

            if text in front_headings:
                new_text = front_headings[text]
                if text != new_text:
                    replace_paragraph_text(p, new_text)
                style_id = "TOCHeading" if new_text == "TABLE OF CONTENTS" else "Heading1"
                set_para_style(p, style_id)
                set_para_layout(p, align="left", before=240, after=240, line=240)
                set_run_format_on_paragraph(p, font_size_half_points=28, bold=True)
                list_mode = False
                continue

            level = get_level(text)
            if not body_started and text in {"INTRODUCTION", "1. INTRODUCTION"}:
                body_started = True

            if body_started and level == 1:
                chapter_counter += 1
                title = re.sub(r"^\d+\.\s*", "", text).upper()
                new_text = f"{chapter_counter}. {title}"
                if text != new_text:
                    replace_paragraph_text(p, new_text)
                set_para_style(p, "Heading1")
                set_para_layout(p, align="left", before=240, after=240, line=240)
                set_run_format_on_paragraph(p, font_size_half_points=28, bold=True)
                list_mode = False
                continue

            if level == 2:
                set_para_style(p, "Heading2")
                set_para_layout(p, align="left", before=240, after=240, line=240)
                set_run_format_on_paragraph(p, font_size_half_points=24, bold=True)
                list_mode = False
                continue

            if level == 3:
                set_para_style(p, "Heading3")
                set_para_layout(p, align="left", before=240, after=240, line=240)
                set_run_format_on_paragraph(p, font_size_half_points=24, bold=True)
                list_mode = False
                continue

            if re.match(r"^(Figure|Table)\s*\d+", text):
                set_para_style(p, "Caption")
                set_para_layout(p, align="center", before=120, after=120, line=240)
                set_run_format_on_paragraph(p, font_size_half_points=24, bold=False)
                list_mode = False
                continue

            set_para_style(p, "Normal")
            set_run_format_on_paragraph(p, font_size_half_points=24, bold=False)

            if text.endswith(":"):
                remove_numpr(p)
                set_para_layout(p, align="both", before=0, after=120, line=360)
                list_mode = True
                continue

            if list_mode and is_short_list_item(text):
                set_numpr(p, num_id="16", ilvl="0")
                set_para_layout(p, align="left", before=0, after=60, line=360)
                continue

            list_mode = False
            remove_numpr(p)
            set_para_layout(p, align="both", before=0, after=120, line=360)

        for tbl in body.iterfind(".//w:tbl", NS):
            rows = tbl.findall("w:tr", NS)
            for row_index, tr in enumerate(rows):
                for p in tr.findall(".//w:p", NS):
                    set_para_style(p, "Normal")
                    set_para_layout(p, align="left", before=0, after=0, line=240)
                    set_run_format_on_paragraph(p, font_size_half_points=24, bold=(row_index == 0))

        top_children = list(body)
        for idx in range(len(top_children) - 2, 0, -1):
            current = top_children[idx]
            if current.tag != qn("w:p"):
                continue
            if approval_pos is not None and idx < approval_pos:
                continue
            current_text = paragraph_text(current)
            if current_text:
                continue
            if any(current.findall(path, NS) for path in [".//w:br", ".//w:drawing", ".//w:sectPr"]):
                continue

            prev_elem = top_children[idx - 1]
            next_elem = top_children[idx + 1]
            prev_text = paragraph_text(prev_elem) if prev_elem.tag == qn("w:p") else "TABLE"
            next_text = paragraph_text(next_elem) if next_elem.tag == qn("w:p") else "TABLE"
            if prev_text and next_text:
                body.remove(current)
                top_children.pop(idx)

        for sect in body.findall(".//w:sectPr", NS):
            pgmar = sect.find("w:pgMar", NS)
            if pgmar is None:
                pgmar = ET.SubElement(sect, qn("w:pgMar"))
            pgmar.set(qn("w:top"), "1440")
            pgmar.set(qn("w:right"), "1440")
            pgmar.set(qn("w:bottom"), "1440")
            pgmar.set(qn("w:left"), "1440")
            pgmar.set(qn("w:header"), "720")
            pgmar.set(qn("w:footer"), "720")
            pgmar.set(qn("w:gutter"), "0")

        update_fields = settings_root.find("w:updateFields", NS)
        if update_fields is None:
            update_fields = ET.SubElement(settings_root, qn("w:updateFields"))
        update_fields.set(qn("w:val"), "true")

        footer_tree = ET.ElementTree(create_footer_page_number())

        doc_tree.write(document_xml, encoding="UTF-8", xml_declaration=True)
        style_tree.write(styles_xml, encoding="UTF-8", xml_declaration=True)
        settings_tree.write(settings_xml, encoding="UTF-8", xml_declaration=True)
        footer_tree.write(footer_xml, encoding="UTF-8", xml_declaration=True)

        temp_output = DOC_PATH.with_name(f"{DOC_PATH.stem} - formatted{DOC_PATH.suffix}")
        with zipfile.ZipFile(temp_output, "w", zipfile.ZIP_DEFLATED) as zout:
            for folder, _, files in os.walk(temp_dir):
                for file_name in files:
                    file_path = Path(folder) / file_name
                    arcname = file_path.relative_to(temp_dir).as_posix()
                    zout.write(file_path, arcname)

        shutil.copy2(temp_output, DOC_PATH)
        print(f"Formatted document saved: {DOC_PATH}")
        print(f"Backup created: {backup_path}")
        print(f"Formatted copy: {temp_output}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
