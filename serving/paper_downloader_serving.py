from typing import List, Optional
from dataflow.logger import get_logger

class PaperDownloaderServing():
    """Wraps PubMed/Sci-Hub paper download utilities as a Serving implementation.

    Requirements:
    - biopython==1.85 (install with: pip install biopython==1.85)

    Modes:
    - "query": treat each input as a PubMed query string.
    - "doi": treat each input as a DOI string.
    - "pmid": treat each input as a PMID string.

    The method generate_from_input returns a list[str] status messages or produced file paths.
    """

    def __init__(
        self,
        unpaywall_email: str,
        entrez_email: str,
        entrez_api_key: str,
    ) -> None:
        self.logger = get_logger()

        # Validate required parameters
        if not unpaywall_email:
            raise ValueError("unpaywall_email is required and must not be empty")
        if not entrez_email:
            raise ValueError("entrez_email is required and must not be empty")
        if not entrez_api_key:
            raise ValueError("entrez_api_key is required and must not be empty")

        try:
            # Delayed import to avoid hard dependency when unused
            from dataflow.utils.paper_downloader.PubMedPDFDownloader import PubMedPDFDownloader
            from Bio import Entrez
        except ImportError as e:
            self.logger.error(
                f"Failed to import downloader dependencies: {e}\n"
                "Please install required packages: pip install biopython==1.85"
            )

        Entrez.email = entrez_email
        Entrez.api_key = entrez_api_key
        self.downloader = PubMedPDFDownloader()
        setattr(self.downloader, "email", unpaywall_email)

    def start_serving(self) -> None:
        self.logger.info("PaperDownloaderServing: no local service to start.")
        return

    def download_paper_by_query(self, query: str, retmax: Optional[int] = None):
        if retmax is None:
            return self.downloader.download_papers_by_query(query)
        else:
            return self.downloader.download_papers_by_query(query, retmax=retmax)

    def download_paper_by_dois(self, dois: List[str]):
        return self.downloader.download_papers_by_dois(dois)

    def download_paper_by_pmids(self, pmids: List[str]):
        return self.downloader.download_papers_by_pmids(pmids)

    def generate_from_input(self, user_inputs: List[str], mode: str = "") -> List[str]:
        """Execute downloads according to mode. Returns list of file path strings per input.

        For mode=query, each input may download multiple PDFs; returned entry will be a
        semicolon-joined string of file paths for that query (or empty string if none).
        For mode=doi/pmid, each input maps to one expected PDF path string (or empty string).
        """
        mode = mode.lower().strip()
        results: List[str] = []

        if mode not in {"query", "doi", "pmid"}:
            self.logger.error(f"Unsupported mode '{mode}'. Use 'query', 'doi', or 'pmid'.")
            return ["error: unsupported mode"] * max(1, len(user_inputs))

        try:
            if mode == "query":
                for q in user_inputs:
                    batch = self.downloader.download_papers_by_query(q)
                    filepaths = [item.get('filepath') for item in (batch or []) if item.get('filepath')]
                    results.append(";".join(filepaths))
            elif mode == "doi":
                batch = self.downloader.download_papers_by_dois(user_inputs)
                doi_to_path = {}
                for item in (batch or []):
                    doi = item.get('doi')
                    fp = item.get('filepath')
                    if doi and fp and doi not in doi_to_path:
                        doi_to_path[doi] = fp
                for doi in user_inputs:
                    results.append(doi_to_path.get(doi, ""))
            elif mode == "pmid":
                inputs = [str(p) for p in user_inputs]
                batch = self.downloader.download_papers_by_pmids(inputs)
                pmid_to_path = {}
                for item in (batch or []):
                    pmid = str(item.get('pmid')) if item.get('pmid') is not None else None
                    fp = item.get('filepath')
                    if pmid and fp and pmid not in pmid_to_path:
                        pmid_to_path[pmid] = fp
                for pmid in inputs:
                    results.append(pmid_to_path.get(pmid, ""))
        except Exception as e:
            self.logger.error(f"Download error: {e}")
            raise e

        return results

    def cleanup(self) -> None:
        self.logger.info("Cleaning up resources in PaperDownloaderServing")
        return


