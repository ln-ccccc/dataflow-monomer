from Bio import Entrez
import requests
import time
import os
import re
from urllib.parse import urljoin
from io import BytesIO
from bs4 import BeautifulSoup
from dataflow import get_logger

class PubMedPDFDownloader:
    def __init__(self):
        self.logger = get_logger()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        self.scihub_urls = [
        "https://sci-hub.st/",
        "http://sci-hub.ren/",
        "http://sci-hub.wf/",
        "https://sci-hub.ren/",
        "https://sci-hub.wf/",
        "http://sci-hub.ee",
        "https://sci-hub.ee",
        "http://sci-hub.se",
        "https://sci-hub.se",
        # "https://sci-hub.click",
        "https://sci-hub.cat",
    ]
    
    def search_pubmed(self, query, retmax=1000):
        """搜索PubMed并获取PMID列表"""
        self.logger.info(f"搜索PubMed: {query}")
        try:
            handle = Entrez.esearch(db="pubmed", term=query, retmax=retmax)
            results = Entrez.read(handle)
            handle.close()
            pmids = results['IdList']
            self.logger.info(f"找到 {len(pmids)} 篇文献")
            return pmids
        except Exception as e:
            self.logger.error(f"搜索PubMed时出错: {e}")
            return []
    
    def get_paper_details(self, pmids):
        """获取文献详细信息，包括DOI"""
        self.logger.info("获取文献详细信息...")
        papers = []
        
        # 分批处理，避免一次请求太多
        batch_size = 100
        for i in range(0, len(pmids), batch_size):
            batch_pmids = pmids[i:i+batch_size]
            try:
                handle = Entrez.efetch(db="pubmed", id=batch_pmids, rettype="xml", retmode="xml")
                raw_content = handle.read()
                # 可选：调试输出原始XML
                # print(raw_content.decode('utf-8', errors='ignore'))
                handle.close()

                # 使用 BytesIO 重新构造句柄供 Entrez.read 解析
                records = Entrez.read(BytesIO(raw_content))

                for record in records.get('PubmedArticle', []):
                    paper_info = self.extract_paper_info(record)
                    if paper_info:
                        papers.append(paper_info)
                        
                self.logger.info(f"已处理 {min(i+batch_size, len(pmids))}/{len(pmids)} 篇文献")
                time.sleep(0.5)  # 避免请求过快
                
            except Exception as e:
                self.logger.error(f"获取文献详情时出错: {e}")
                continue
        
        return papers
    
    def extract_paper_info(self, record):
        """从PubMed记录中提取文献信息"""
        try:
            article = record['MedlineCitation']['Article']
            pmid = record['MedlineCitation']['PMID']
            
            # 标题
            title = article.get('ArticleTitle', 'No Title')
            
            # DOI
            doi = None
            if 'ELocationID' in article:
                for elocation in article['ELocationID']:
                    if elocation.attributes.get('EIdType') == 'doi':
                        doi = str(elocation)
                        break
            
            # 作者
            authors = []
            if 'AuthorList' in article:
                for author in article['AuthorList']:
                    if 'LastName' in author and 'ForeName' in author:
                        authors.append(f"{author['LastName']}, {author['ForeName']}")
            
            # 期刊
            journal = article.get('Journal', {}).get('Title', 'Unknown Journal')
            
            # 年份
            year = None
            if 'Journal' in article and 'JournalIssue' in article['Journal']:
                pub_date = article['Journal']['JournalIssue'].get('PubDate', {})
                year = pub_date.get('Year', 'Unknown Year')
            
            return {
                'pmid': str(pmid),
                'title': title,
                'doi': doi,
                'authors': authors,
                'journal': journal,
                'year': year
            }
        except Exception as e:
            self.logger.error(f"解析文献信息时出错: {e}")
            return None
    
    def get_unpaywall_pdf(self, doi):
        """从Unpaywall API获取开放获取PDF链接"""
        if not doi:
            return None
        email = getattr(self, 'email', None)
        try:
            url = f"https://api.unpaywall.org/v2/{doi}?email={email}"
            response = self.session.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('is_oa', False):
                    best_oa = data.get('best_oa_location')
                    if best_oa and best_oa.get('url_for_pdf'):
                        return best_oa['url_for_pdf']
            
        except Exception as e:
            self.logger.error(f"从Unpaywall获取PDF链接时出错: {e}")
        
        return None
    
    def get_scihub_pdf(self, doi):
        """从Sci-Hub获取PDF"""
        if not doi:
            return None
        
        self.logger.info(f"尝试从Sci-Hub下载: {doi}")
        
        for scihub_url in self.scihub_urls:
            try:
                paper_url = urljoin(scihub_url, doi)
                self.logger.debug(f"尝试URL: {paper_url}")
                
                response = self.session.get(paper_url, timeout=30)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'html.parser')
                    
                    # 查找PDF链接
                    pdf_link = None
                    
                    # 方法1: 查找iframe中的PDF
                    iframe = soup.find('iframe', {'id': 'pdf'})
                    if iframe and iframe.get('src'):
                        pdf_link = iframe['src']
                        self.logger.debug(f"通过iframe找到PDF链接: {pdf_link}")
                    
                    # 方法2: 查找直接的PDF链接按钮
                    if not pdf_link:
                        pdf_buttons = soup.find_all('button', {'onclick': True})
                        for button in pdf_buttons:
                            onclick = button.get('onclick', '')
                            if 'location.href' in onclick:
                                match = re.search(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", onclick)
                                if match:
                                    pdf_link = match.group(1)
                                    self.logger.debug(f"通过按钮找到PDF链接: {pdf_link}")
                                    break
                    
                    # 方法3: 查找包含PDF的链接
                    if not pdf_link:
                        links = soup.find_all('a', href=True)
                        for link in links:
                            href = link['href']
                            if href.endswith('.pdf') or 'pdf' in href.lower():
                                pdf_link = href
                                self.logger.debug(f"通过链接找到PDF链接: {pdf_link}")
                                break
                    
                    # 方法4: 查找embed标签
                    if not pdf_link:
                        embed = soup.find('embed', {'type': 'application/pdf'})
                        if embed and embed.get('src'):
                            pdf_link = embed['src']
                            self.logger.debug(f"通过embed找到PDF链接: {pdf_link}")
                    
                    # 方法5: 查找object标签
                    if not pdf_link:
                        obj = soup.find('object', {'type': 'application/pdf'})
                        if obj and obj.get('data'):
                            pdf_link = obj['data']
                            self.logger.debug(f"通过object找到PDF链接: {pdf_link}")
                    
                    # 方法6: 在JavaScript中查找PDF链接
                    if not pdf_link:
                        scripts = soup.find_all('script')
                        for script in scripts:
                            if script.string:
                                # 查找JavaScript中的PDF URL
                                pdf_matches = re.findall(r'["\']([^"\']*\.pdf[^"\']*)["\']', script.string)
                                for match in pdf_matches:
                                    if 'http' in match or match.startswith('/'):
                                        pdf_link = match
                                        self.logger.debug(f"通过JavaScript找到PDF链接: {pdf_link}")
                                        break
                                if pdf_link:
                                    break
                    
                    if pdf_link:
                        # 清理和修复PDF链接
                        pdf_link = pdf_link.replace('\\/', '/')  # 修复转义字符
                        pdf_link = pdf_link.replace('\\//', '//')  # 修复双转义
                        pdf_link = pdf_link.strip()  # 去除空格
                        
                        # 确保是完整URL
                        if pdf_link.startswith('//'):
                            pdf_link = 'https:' + pdf_link
                        elif pdf_link.startswith('/'):
                            pdf_link = urljoin(scihub_url, pdf_link)
                        elif not pdf_link.startswith(('http://', 'https://')):
                            pdf_link = urljoin(scihub_url, pdf_link)
                        
                        self.logger.info(f"最终PDF链接: {pdf_link}")
                        
                        # 验证URL格式
                        if '://' in pdf_link and not pdf_link.startswith('https:\\/\\/'):
                            return pdf_link
                        else:
                            self.logger.warning(f"PDF链接格式异常，跳过: {pdf_link}")
                            continue
                
            except Exception as e:
                self.logger.error(f"从 {scihub_url} 获取PDF时出错: {e}")
                continue
        
        return None
    
    def download_pdf(self, pdf_url, filename, max_retries=3):
        """下载PDF文件，成功返回文件路径，失败返回None"""
        if not pdf_url:
            return None
        
        for attempt in range(max_retries):
            try:
                self.logger.info(f"下载PDF (尝试 {attempt + 1}/{max_retries}): {filename}")
                
                response = self.session.get(pdf_url, timeout=60, stream=True)
                response.raise_for_status()
                
                # 检查内容类型
                content_type = response.headers.get('content-type', '').lower()
                if 'pdf' not in content_type and len(response.content) < 1000:
                    self.logger.warning("下载的文件可能不是PDF")
                    continue
                
                # 确保下载目录存在
                os.makedirs(self.download_dir, exist_ok=True)
                filepath = os.path.join(self.download_dir, filename)
                
                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                
                self.logger.info(f"成功下载: {filepath}")
                return filepath
                
            except Exception as e:
                self.logger.error(f"下载失败 (尝试 {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2)
        
        return None
    
    def safe_filename(self, filename):
        """创建安全的文件名"""
        # 移除或替换不安全的字符
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        filename = filename.replace('\n', ' ').replace('\r', ' ')
        # 限制长度
        if len(filename) > 200:
            filename = filename[:200]
        return filename.strip()
    
    def download_papers_by_query(self, query, retmax=100):
        """通过查询搜索并下载文献PDF"""
        self.logger.info("开始文献搜索和下载流程...")
        
        # 1. 搜索PubMed
        pmids = self.search_pubmed(query, retmax)
        if not pmids:
            self.logger.info("没有找到相关文献")
            return
        
        # 2. 获取文献详细信息
        papers = self.get_paper_details(pmids)
        if not papers:
            self.logger.info("无法获取文献详细信息")
            return
        
        self.logger.info(f"获取到 {len(papers)} 篇文献的详细信息")
        
        # 3. 下载PDF
        return self._download_papers_batch(papers)
    
    def download_papers_by_dois(self, dois):
        """通过DOI列表下载文献PDF"""
        self.logger.info(f"开始通过DOI下载文献，共 {len(dois)} 个DOI...")
        
        papers = []
        for i, doi in enumerate(dois):
            if not doi or not doi.strip():
                continue
                
            doi = doi.strip()
            self.logger.info(f"处理DOI {i+1}/{len(dois)}: {doi}")
            
            # 尝试通过DOI获取文献信息
            paper_info = self.get_paper_info_by_doi(doi)
            if paper_info:
                papers.append(paper_info)
            else:
                # 如果无法获取详细信息，创建基本信息
                papers.append({
                    'pmid': None,
                    'title': f'Paper with DOI {doi}',
                    'doi': doi,
                    'authors': [],
                    'journal': 'Unknown Journal',
                    'year': 'Unknown Year'
                })
        
        if papers:
            self.logger.info(f"准备下载 {len(papers)} 篇文献")
            return self._download_papers_batch(papers)
        else:
            self.logger.warning("没有有效的DOI可供下载")
            return []
    
    def get_paper_info_by_doi(self, doi):
        """通过DOI获取文献基本信息"""
        try:
            # 尝试通过PubMed搜索DOI
            search_query = f'"{doi}"[DOI]'
            handle = Entrez.esearch(db="pubmed", term=search_query, retmax=1)
            results = Entrez.read(handle)
            handle.close()
            
            if results['IdList']:
                pmid = results['IdList'][0]
                handle = Entrez.efetch(db="pubmed", id=pmid, rettype="xml", retmode="xml")
                records = Entrez.read(handle)
                handle.close()
                
                if records['PubmedArticle']:
                    return self.extract_paper_info(records['PubmedArticle'][0])
        except Exception as e:
            self.logger.error(f"通过DOI获取PubMed信息失败: {e}")
        
        return None
    
    def _download_papers_batch(self, papers):
        """批量下载文献PDF的核心逻辑

        返回列表，每项包含 { 'pmid': str|None, 'doi': str|None, 'filepath': str|None }
        """
        success_count = 0
        failed_papers = []
        results = []
        
        for i, paper in enumerate(papers):
            self.logger.info(f"\n处理第 {i+1}/{len(papers)} 篇文献:")
            self.logger.info(f"标题: {paper['title'][:100]}...")
            self.logger.info(f"DOI: {paper['doi']}")
            self.logger.info(f"PMID: {paper['pmid']}")
            
            if not paper['doi']:
                print("没有DOI，跳过")
                failed_papers.append(paper)
                continue
            
            # 创建文件名
            authors_str = paper['authors'][0] if paper['authors'] else 'Unknown'
            # filename = f"{paper['year']}_{authors_str}_{paper['title'][:50]}.pdf"
            filename = f"{paper['year']}_PMID{paper['pmid']}.pdf"
            filename = self.safe_filename(filename)
            
            # 检查文件是否已存在
            filepath = os.path.join(self.download_dir, filename)
            if os.path.exists(filepath):
                self.logger.info(f"文件已存在，跳过: {filename}")
                success_count += 1
                results.append({'pmid': paper['pmid'], 'doi': paper['doi'], 'filepath': filepath})
                continue
            
            # 尝试从Unpaywall下载
            pdf_url = self.get_unpaywall_pdf(paper['doi'])
            if pdf_url:
                self.logger.info("找到开放获取版本")
                dl_path = self.download_pdf(pdf_url, filename)
                if dl_path:
                    success_count += 1
                    results.append({'pmid': paper['pmid'], 'doi': paper['doi'], 'filepath': dl_path})
                    time.sleep(1)
                    continue
            
            # 尝试从Sci-Hub下载
            pdf_url = self.get_scihub_pdf(paper['doi'])
            if pdf_url:
                dl_path = self.download_pdf(pdf_url, filename)
                if dl_path:
                    success_count += 1
                    results.append({'pmid': paper['pmid'], 'doi': paper['doi'], 'filepath': dl_path})
                else:
                    failed_papers.append(paper)
            else:
                self.logger.warning("无法获取PDF链接")
                failed_papers.append(paper)
            
            time.sleep(2)  # 避免请求过频
        
        # 输出结果统计
        self.logger.info(f"\n下载完成!")
        self.logger.info(f"总文献数: {len(papers)}")
        self.logger.info(f"成功下载: {success_count}")
        self.logger.info(f"下载失败: {len(failed_papers)}")
        
        if failed_papers:
            self.logger.info("\n下载失败的文献:")
            for paper in failed_papers:
                self.logger.info(f"- {paper['title'][:100]}... (DOI: {paper['doi']})")

        return results
    
    def download_papers_by_pmids(self, pmids):
        """通过PMID列表下载文献PDF"""
        self.logger.info(f"开始通过PMID下载文献，共 {len(pmids)} 个PMID...")
        
        # 获取文献详细信息
        papers = self.get_paper_details(pmids)
        if not papers:
            self.logger.info("无法获取文献详细信息")
            return
        
        self.logger.info(f"获取到 {len(papers)} 篇文献的详细信息")
        return self._download_papers_batch(papers)
    
    def download_single_pmid(self, pmid):
        """下载单个PMID的文献"""
        self.logger.info(f"下载单个PMID: {pmid}")
        return self.download_papers_by_pmids([pmid])
    
    def download_single_doi(self, doi):
        """下载单个DOI的文献"""
        self.logger.info(f"下载单个DOI: {doi}")
        return self.download_papers_by_dois([doi])
    
    def load_dois_from_file(self, filepath):
        """从文件加载DOI列表"""
        dois = []
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):  # 忽略空行和注释
                        dois.append(line)
            self.logger.info(f"从文件 {filepath} 加载了 {len(dois)} 个DOI")
            return dois
        except Exception as e:
            self.logger.error(f"从文件加载DOI时出错: {e}")
            return []
        
    def load_pmids_from_file(self, filepath):
        pmids = []
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                pmid = line.strip()
                if pmid:
                    pmids.append(pmid)
        return pmids


