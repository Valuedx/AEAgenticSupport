"""
Robust document processor for RAG (Feature 2.1).
Handles PDF, Markdown, and JSON with layout-aware extraction.
"""
import os
import json
import logging
import pdfplumber
from typing import List, Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger("ops_agent.rag.processor")

@dataclass
class DocumentChunk:
    id: str
    content: str
    metadata: Dict

class DocumentProcessor:
    """Enterprise document processor with support for tables and semantic chunking."""

    def __init__(self, chunk_size: int = 1500, chunk_overlap: int = 200):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def process_file(self, filepath: str) -> List[DocumentChunk]:
        """Process a file into one or more searchable chunks based on its extension."""
        ext = os.path.splitext(filepath)[1].lower()
        
        try:
            if ext == ".pdf":
                return self._process_pdf(filepath)
            elif ext == ".md":
                return self._process_markdown(filepath)
            elif ext == ".json":
                return self._process_json(filepath)
            else:
                logger.warning(f"Unsupported file type: {ext} for {filepath}")
                return []
        except Exception as e:
            logger.error(f"Failed to process {filepath}: {e}")
            return []

    def _process_pdf(self, filepath: str) -> List[DocumentChunk]:
        """Extract text and tables from PDF, preserving layout where possible."""
        chunks = []
        base_id = os.path.splitext(os.path.basename(filepath))[0]
        
        with pdfplumber.open(filepath) as pdf:
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text() or ""
                
                # Extract and format tables
                tables = page.extract_tables()
                table_md = ""
                for table in tables:
                    if not any(table): continue # skip empty
                    table_md += "\n\n| " + " | ".join([str(c or "").replace("\n", " ") for c in (table[0] or [])]) + " |\n"
                    table_md += "| " + " | ".join(["---"] * len(table[0] or [])) + " |\n"
                    for row in table[1:]:
                        table_md += "| " + " | ".join([str(c or "").replace("\n", " ") for c in (row or [])]) + " |\n"

                combined_content = f"Page {i+1}\n\n{page_text}\n{table_md}"
                
                # Chunk the page if it's too large
                page_chunks = self._chunk_text(combined_content)
                for j, content in enumerate(page_chunks):
                    chunks.append(DocumentChunk(
                        id=f"pdf-{base_id}-p{i+1}-c{j+1}",
                        content=content.strip(),
                        metadata={
                            "source": filepath,
                            "type": "pdf",
                            "page": i + 1,
                            "chunk": j + 1
                        }
                    ))
        return chunks

    def _process_markdown(self, filepath: str) -> List[DocumentChunk]:
        """Process Markdown files with semantic awareness."""
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        
        base_id = os.path.splitext(os.path.basename(filepath))[0]
        chunks = self._chunk_text(content)
        
        return [
            DocumentChunk(
                id=f"md-{base_id}-c{i+1}",
                content=chunk.strip(),
                metadata={"source": filepath, "type": "markdown", "chunk": i+1}
            )
            for i, chunk in enumerate(chunks)
        ]

    def _process_json(self, filepath: str) -> List[DocumentChunk]:
        """Process structured JSON knowledge items."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        chunks = []
        base_id = os.path.splitext(os.path.basename(filepath))[0]
        
        # Flatten or process depending on structure
        items = data if isinstance(data, list) else [data]
        for i, item in enumerate(items):
            content = item.get("content") or item.get("body") or json.dumps(item)
            meta = item.get("metadata", {})
            meta.update({"source": filepath, "type": "json"})
            
            chunks.append(DocumentChunk(
                id=item.get("id") or f"json-{base_id}-i{i+1}",
                content=content,
                metadata=meta
            ))
        return chunks

    def _chunk_text(self, text: str) -> List[str]:
        """Recursive character chunking with overlap."""
        if len(text) <= self.chunk_size:
            return [text]
            
        chunks = []
        start = 0
        while start < len(text):
            end = start + self.chunk_size
            
            # Find a good break point (newline or period)
            if end < len(text):
                last_space = text.rfind("\n", start, end)
                if last_space == -1 or last_space < start + (self.chunk_size // 2):
                    last_space = text.rfind(". ", start, end)
                if last_space != -1 and last_space > start:
                    end = last_space + 1
            
            chunks.append(text[start:end])
            start = end - self.chunk_overlap if end < len(text) else end
            if start < 0: start = 0 # sanity check
            
        return chunks
