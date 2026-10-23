"""Retrieve public primary-source pages; retain text and a transport manifest."""
import concurrent.futures, datetime, hashlib, json, pathlib, requests
from bs4 import BeautifulSoup
ROOT = pathlib.Path(__file__).parent
SOURCES = {
'memgpt':'https://arxiv.org/abs/2310.08560',
'generative_agents':'https://arxiv.org/abs/2304.03442',
'memorybank':'https://ojs.aaai.org/index.php/AAAI/article/view/29946',
'locomo_paper':'https://aclanthology.org/2024.acl-long.747/',
'locomo_repo':'https://raw.githubusercontent.com/snap-research/locomo/main/README.md',
'locomo_license':'https://raw.githubusercontent.com/snap-research/locomo/main/LICENSE',
'zep':'https://arxiv.org/abs/2501.13956',
'graphiti_readme':'https://raw.githubusercontent.com/getzep/graphiti/main/README.md',
'graphiti_license':'https://raw.githubusercontent.com/getzep/graphiti/main/LICENSE',
'mem0':'https://arxiv.org/abs/2504.19413',
'mem0_readme':'https://raw.githubusercontent.com/mem0ai/mem0/main/README.md',
'mem0_license':'https://raw.githubusercontent.com/mem0ai/mem0/main/LICENSE',
'rag':'https://arxiv.org/abs/2005.11401',
'lost_middle':'https://aclanthology.org/2024.tacl-1.9/',
'longmemeval':'https://arxiv.org/abs/2410.10813',
'longmemeval_repo':'https://raw.githubusercontent.com/xiaowu0162/LongMemEval/main/README.md',
'longmemeval_license':'https://raw.githubusercontent.com/xiaowu0162/LongMemEval/main/LICENSE',
'bm25':'https://www.nowpublishers.com/article/Details/INR-019',
'snodgrass':'https://www2.cs.arizona.edu/~rts/tdbbook.pdf',
'rrf':'https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf',
'poisonedrag':'https://www.usenix.org/conference/usenixsecurity25/presentation/zou',
'gdpr':'https://eur-lex.europa.eu/eli/reg/2016/679/oj/eng',
'judge':'https://arxiv.org/abs/2306.05685',
'postgres_ranges':'https://www.postgresql.org/docs/16/rangetypes.html',
'postgres_rls':'https://www.postgresql.org/docs/16/ddl-rowsecurity.html',
'postgres_fts':'https://www.postgresql.org/docs/16/textsearch-controls.html',
'pgvector':'https://raw.githubusercontent.com/pgvector/pgvector/master/README.md',
'amem':'https://arxiv.org/abs/2502.12110',
'memos':'https://arxiv.org/abs/2507.03724',
'memoryos':'https://arxiv.org/abs/2506.06326',
'agent_poison':'https://arxiv.org/abs/2407.12784',
'memorygraft':'https://arxiv.org/abs/2512.16962',
'locomo_eval':'https://raw.githubusercontent.com/snap-research/locomo/main/task_eval/evaluation.py',
'lme_eval':'https://raw.githubusercontent.com/xiaowu0162/LongMemEval/main/src/evaluation/evaluate_qa.py',
}
def fetch(item):
 key,url=item
 try:
  r=requests.get(url,timeout=65,headers={'User-Agent':'AcademicProposalVerification/1.0'})
  kind='pdf' if r.content.startswith(b'%PDF') else 'txt'
  p=ROOT/f'{key}.{kind}'
  if kind=='pdf': p.write_bytes(r.content)
  else:
   soup=BeautifulSoup(r.text,'html.parser')
   for x in soup(['script','style','nav']):x.decompose()
   p.write_text(soup.get_text('\n',strip=True) if '<html' in r.text[:2000].lower() else r.text)
  return {'key':key,'requested_url':url,'final_url':r.url,'http_status':r.status_code,'retrieved_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'file':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
 except Exception as e:return {'key':key,'requested_url':url,'error':str(e)}
if __name__=='__main__':
 with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex: rows=list(ex.map(fetch,SOURCES.items()))
 (ROOT/'retrieval_manifest.json').write_text(json.dumps(rows,indent=2))
 for row in rows:print(row['key'],row.get('http_status',row.get('error')),row.get('file'))
