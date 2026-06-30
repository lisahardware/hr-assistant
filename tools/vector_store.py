"""
tools/vector_store.py
Manages the ChromaDB vector store for HR templates and compliance documents.
Runs entirely locally — no data leaves the machine.
"""

import os
import json
import chromadb
from chromadb.utils import embedding_functions


class VectorStore:
    def __init__(self, persist_path: str = "./hr_vector_store"):
        self.client = chromadb.PersistentClient(path=persist_path)
        
        # Use the default sentence transformer for local embeddings
        # This runs entirely on your machine with no API calls
        self.embedding_fn = embedding_functions.DefaultEmbeddingFunction()

        # Two collections: internal templates and compliance sources
        self.templates = self.client.get_or_create_collection(
            name="hr_templates",
            embedding_function=self.embedding_fn,
            metadata={"description": "Owner HR document templates"}
        )
        self.compliance = self.client.get_or_create_collection(
            name="compliance_sources",
            embedding_function=self.embedding_fn,
            metadata={"description": "Public labor law and compliance references"}
        )

    def index_template(self, file_path: str) -> int:
        """
        Index an HR template file into the vector store.
        Chunks by section (lines starting with ##) to preserve
        semantic boundaries rather than splitting arbitrarily.
        Returns the number of chunks indexed.
        """
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        chunks = self._chunk_by_section(content)
        file_name = os.path.basename(file_path)

        for i, chunk in enumerate(chunks):
            chunk_id = f"{file_name}_chunk_{i}"
            # Check if already indexed to avoid duplicates on re-run
            existing = self.templates.get(ids=[chunk_id])
            if existing["ids"]:
                self.templates.update(
                    ids=[chunk_id],
                    documents=[chunk["content"]],
                    metadatas=[{
                        "source": file_name,
                        "section": chunk["heading"],
                        "file_path": file_path
                    }]
                )
            else:
                self.templates.add(
                    ids=[chunk_id],
                    documents=[chunk["content"]],
                    metadatas=[{
                        "source": file_name,
                        "section": chunk["heading"],
                        "file_path": file_path
                    }]
                )

        return len(chunks)

    def index_compliance_text(self, text: str, source_url: str, state: str, retrieved_date: str) -> int:
        """
        Index a compliance document from a public source.
        Called by the compliance refresh tool on a schedule.
        """
        chunks = self._chunk_by_section(text)

        for i, chunk in enumerate(chunks):
            chunk_id = f"compliance_{state}_{i}"
            existing = self.compliance.get(ids=[chunk_id])
            if existing["ids"]:
                self.compliance.update(
                    ids=[chunk_id],
                    documents=[chunk["content"]],
                    metadatas=[{
                        "source": source_url,
                        "state": state,
                        "retrieved_date": retrieved_date,
                        "section": chunk["heading"]
                    }]
                )
            else:
                self.compliance.add(
                    ids=[chunk_id],
                    documents=[chunk["content"]],
                    metadatas=[{
                        "source": source_url,
                        "state": state,
                        "retrieved_date": retrieved_date,
                        "section": chunk["heading"]
                    }]
                )

        return len(chunks)

    def retrieve_templates(self, query: str, n_results: int = 4, where: dict = None) -> list[dict]:
        """
        Semantic search over internal HR templates.
        Optionally filter by metadata using a where clause.
        """
        count = self.templates.count()
        if count == 0:
            return []
        
        kwargs = {
            "query_texts": [query],
            "n_results": min(n_results, count)
        }
        if where:
            kwargs["where"] = where

        results = self.templates.query(**kwargs)
        return self._format_results(results)

    def retrieve_compliance(self, query: str, state: str = None, n_results: int = 4) -> list[dict]:
        """
        Semantic search over compliance documents.
        Optionally filter by state for jurisdiction-specific results.
        """
        where = {"state": state} if state else None
        count = self.compliance.count()
        if count == 0:
            return []

        results = self.compliance.query(
            query_texts=[query],
            n_results=min(n_results, count),
            where=where
        )
        return self._format_results(results)

    def _chunk_by_section(self, text: str) -> list[dict]:
        """
        Split document text at section headings (## Header).
        Falls back to the full document as one chunk if no headings found.
        """
        lines = text.split("\n")
        chunks = []
        current_heading = "Introduction"
        current_lines = []

        for line in lines:
            if line.startswith("## "):
                if current_lines:
                    chunks.append({
                        "heading": current_heading,
                        "content": "\n".join(current_lines).strip()
                    })
                current_heading = line.replace("## ", "").strip()
                current_lines = []
            else:
                current_lines.append(line)

        if current_lines:
            chunks.append({
                "heading": current_heading,
                "content": "\n".join(current_lines).strip()
            })

        # Filter out empty chunks
        return [c for c in chunks if c["content"]]

    def _format_results(self, results: dict) -> list[dict]:
        """Format ChromaDB query results into a clean list of dicts."""
        formatted = []
        if not results["documents"] or not results["documents"][0]:
            return formatted
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            formatted.append({
                "content": doc,
                "metadata": meta
            })
        return formatted

    def index_all_templates(self, templates_dir: str = None) -> dict:
        """Index all template files in the templates directory."""
        if templates_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            templates_dir = os.path.join(base_dir, "templates")
        
        results = {}
        for filename in os.listdir(templates_dir):
            if filename.endswith(".txt") or filename.endswith(".docx"):
                path = os.path.join(templates_dir, filename)
                count = self.index_template(path)
                results[filename] = count
        return results