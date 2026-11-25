"""Portable Word export using the standard OOXML package format."""
import hashlib
from pathlib import Path
import re
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED


def write_word(markdown_path):
    markdown_path = Path(markdown_path)
    text = markdown_path.read_text(encoding='utf-8')
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    dest = markdown_path.with_suffix('.docx')
    marker = dest.with_suffix('.docx.sha256')
    if dest.exists() and marker.exists():
        previous = marker.read_text().split()
        if previous[0] == content_hash:
            return dest
        if len(previous) < 2 or hashlib.sha256(dest.read_bytes()).hexdigest() != previous[1]:
            dest = dest.with_name(dest.stem+' 更新-'+content_hash[:8]+'.docx')
            marker = dest.with_suffix('.docx.sha256')
            if dest.exists():
                return dest
    elif dest.exists():
        dest = dest.with_name(dest.stem+' 更新-'+content_hash[:8]+'.docx')
        marker = dest.with_suffix('.docx.sha256')
        if dest.exists(): return dest
    text = re.sub(r'\A---\n.*?\n---\n', '', text, flags=re.S)
    paragraphs = []
    for line in text.splitlines():
        if line.strip() in ('<details>', '</details>') or line.startswith('```'):
            continue
        line = re.sub(r'</?summary>', '', line)
        heading = re.match(r'^(#{1,6})\s+', line)
        size = 32 if heading else 22
        line = re.sub(r'^#{1,6}\s+', '', line)
        line = re.sub(r'!?\[([^\]]+)\]\(([^)]+)\)', r'\1（\2）', line)
        runs = []
        for i, part in enumerate(line.split('**')):
            bold = '<w:b/>' if heading or i % 2 else ''
            runs.append(f'<w:r><w:rPr>{bold}<w:rFonts w:eastAsia="宋体"/><w:sz w:val="{size}"/></w:rPr><w:t xml:space="preserve">{escape(part)}</w:t></w:r>')
        paragraphs.append('<w:p><w:pPr><w:spacing w:after="120"/></w:pPr>'+''.join(runs)+'</w:p>')
    document = '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+''.join(paragraphs)+'<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:bottom="1440" w:left="1440" w:right="1440"/></w:sectPr></w:body></w:document>'
    temp = dest.with_suffix('.docx.tmp')
    with ZipFile(temp, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr('word/document.xml', document)
    temp.replace(dest)
    marker.write_text(content_hash+' '+hashlib.sha256(dest.read_bytes()).hexdigest())
    return dest
