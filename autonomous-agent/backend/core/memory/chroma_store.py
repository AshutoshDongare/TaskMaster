"""
ChromaDB Long-Term Memory Store.

Semantic vector memory for navigation patterns, login procedures,
data locations, failure patterns, and user preferences.

CRITICAL: Only store memories after Verifier confirms success.
Bad memories make the system worse over time.
"""

import logging
import uuid
from typing import Optional, List, Dict, Any

import chromadb
from chromadb.config import Settings

logger = logging.getLogger(__name__)


class LongTermMemory:
    """
    Semantic vector memory using ChromaDB.

    WHAT GETS STORED (only after Verifier confirms success):

    Type: navigation_path
      Content: "To renew domain on Namecheap: Login > Domain List >
               Find domain > Click Renew > Add to Cart > Checkout > Confirm"
      Site: "namecheap.com"

    Type: login_pattern
      Content: "Zerodha login: zerodha.com/login > Enter user_id (not email) >
               Password > TOTP required via Google Authenticator app.
               Dashboard shows 'Holdings' tab in left nav."
      Site: "zerodha.com"

    Type: data_location
      Content: "Portfolio value on Groww: After login, Dashboard page shows
               'Current Value' in bold at top. Stocks tab shows individual holdings."
      Site: "groww.in"

    Type: failure_pattern
      Content: "FAILURE: Searching 'tax documents' on HDFC Bank portal returns no results.
               CORRECT: Use 'eStatements' in the Accounts menu."
      Site: "hdfcbank.com"

    Type: user_preference
      Content: "User prefers portfolio values in INR with comma formatting."
      Site: None (global)

    RECALL: Automatically search and inject relevant memories into agent
    context at the start of each sub-task involving a known site.
    """

    COLLECTION_NAME = "agent_memory"

    def __init__(self, chroma_url: str = "http://localhost:8001"):
        self.chroma_url = chroma_url
        self._client: Optional[chromadb.HttpClient] = None
        self._collection = None

    def connect(self):
        """Initialize ChromaDB connection."""
        self._client = chromadb.HttpClient(
            host=self.chroma_url.replace("http://", "").split(":")[0],
            port=int(self.chroma_url.split(":")[-1]),
        )
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(f"Connected to ChromaDB at {self.chroma_url}, "
                     f"collection: {self.COLLECTION_NAME}")

    @property
    def collection(self):
        if not self._collection:
            raise RuntimeError("ChromaDB not connected. Call connect() first.")
        return self._collection

    def remember(
        self,
        content: str,
        memory_type: str,
        site: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Store a new memory. Returns the memory ID.

        Args:
            content: The memory text (will be embedded for semantic search)
            memory_type: One of: navigation_path, login_pattern, data_location,
                        failure_pattern, user_preference
            site: Domain name, or None for global preferences
            metadata: Additional structured metadata

        Returns:
            The ChromaDB document ID
        """
        memory_id = str(uuid.uuid4())
        meta = {
            "memory_type": memory_type,
            "site": site or "",
            "usefulness_score": 1.0,
            "times_recalled": 0,
            **(metadata or {}),
        }

        # Ensure all metadata values are strings, ints, floats, or bools
        # (ChromaDB restriction)
        clean_meta = {}
        for k, v in meta.items():
            if isinstance(v, (str, int, float, bool)):
                clean_meta[k] = v
            else:
                clean_meta[k] = str(v)

        self.collection.add(
            ids=[memory_id],
            documents=[content],
            metadatas=[clean_meta],
        )

        logger.info(f"Stored memory [{memory_type}] for site={site}: "
                     f"{content[:80]}...")
        return memory_id

    def recall(
        self,
        query: str,
        n_results: int = 5,
        site_filter: Optional[str] = None,
        memory_type_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Semantic search for relevant memories.

        Args:
            query: Natural language query
            n_results: Maximum results to return
            site_filter: Only return memories for this site
            memory_type_filter: Only return memories of this type

        Returns:
            List of dicts with: id, content, memory_type, site, distance, metadata
        """
        where_filter = {}
        if site_filter:
            where_filter["site"] = site_filter
        if memory_type_filter:
            where_filter["memory_type"] = memory_type_filter

        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where_filter if where_filter else None,
            )
        except Exception as e:
            logger.error(f"Memory recall failed: {e}")
            return []

        memories = []
        if results and results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                memory = {
                    "id": doc_id,
                    "content": results["documents"][0][i],
                    "distance": results["distances"][0][i] if results.get("distances") else None,
                    "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
                }
                memory["memory_type"] = memory["metadata"].get("memory_type", "unknown")
                memory["site"] = memory["metadata"].get("site", "")
                memories.append(memory)

                # Update recall count
                self._increment_recall_count(doc_id)

        logger.info(f"Recalled {len(memories)} memories for query: {query[:60]}...")
        return memories

    def _increment_recall_count(self, memory_id: str):
        """Track how often a memory is recalled (for usefulness scoring)."""
        try:
            result = self.collection.get(ids=[memory_id])
            if result and result["metadatas"]:
                meta = result["metadatas"][0]
                meta["times_recalled"] = int(meta.get("times_recalled", 0)) + 1
                self.collection.update(ids=[memory_id], metadatas=[meta])
        except Exception as e:
            logger.warning(f"Failed to update recall count for {memory_id}: {e}")

    def update_usefulness(self, memory_id: str, was_helpful: bool):
        """
        Update usefulness score based on whether the memory helped.

        Helpful memories get boosted, unhelpful ones decay.
        Memories with score < 0.1 should be considered for deletion.
        """
        try:
            result = self.collection.get(ids=[memory_id])
            if result and result["metadatas"]:
                meta = result["metadatas"][0]
                current_score = float(meta.get("usefulness_score", 1.0))

                if was_helpful:
                    new_score = min(2.0, current_score * 1.1 + 0.1)
                else:
                    new_score = max(0.0, current_score * 0.8 - 0.1)

                meta["usefulness_score"] = new_score
                self.collection.update(ids=[memory_id], metadatas=[meta])
                logger.info(f"Memory {memory_id} usefulness: "
                            f"{current_score:.2f} → {new_score:.2f}")
        except Exception as e:
            logger.warning(f"Failed to update usefulness for {memory_id}: {e}")

    def delete_memory(self, memory_id: str):
        """Delete a specific memory."""
        try:
            self.collection.delete(ids=[memory_id])
            logger.info(f"Deleted memory: {memory_id}")
        except Exception as e:
            logger.error(f"Failed to delete memory {memory_id}: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Get memory store statistics."""
        try:
            count = self.collection.count()
            return {"total_memories": count, "collection": self.COLLECTION_NAME}
        except Exception:
            return {"total_memories": 0, "collection": self.COLLECTION_NAME,
                    "error": "disconnected"}
