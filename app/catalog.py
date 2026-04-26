from pathlib import Path


ARXIV_CS_AI_API_URL = "https://export.arxiv.org/api/query?search_query=cat:cs.AI&sortBy=submittedDate&sortOrder=descending&max_results=50"
ARXIV_CS_SE_API_URL = "https://export.arxiv.org/api/query?search_query=cat:cs.SE&sortBy=submittedDate&sortOrder=descending&max_results=50"
DEFAULT_SOURCE_PACK_PATH = Path(__file__).resolve().parent.parent / "config" / "source-packs" / "default.yaml"
