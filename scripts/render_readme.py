"""Build README motion graphics and extract authentic product media from the demo."""
from pathlib import Path
import subprocess, json, html, hashlib, shutil

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'assets/readme'; OUT.mkdir(parents=True,exist_ok=True)
VIDEO=ROOT/'artifacts/redesign/TrustChain-Cinematic-Product-Tour.mp4'
METADATA=ROOT/'artifacts/redesign/cinematic-tour-metadata.json'
DRIVE='https://drive.google.com/file/d/1sM2GhKTrmnHkojWA4kJU5ybNCBuHza0F/view?usp=sharing'
BG='#1c1511'; PANEL='#2a2119'; LINE='#55432e'; INK='#f5f1e9'; MUTED='#c1b29e'; ACCENT='#b3ef69'; CHALK='#dcc8a5'; GREEN='#92c952'

def clip(name,start,length,width=720):
 # Preserve the full frame, including the recorder's chapter and field-note overlays.
 vf=f'fps=8,scale={width}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4'
 subprocess.run(['ffmpeg','-y','-loglevel','error','-ss',str(start),'-t',str(length),'-i',str(VIDEO),'-filter_complex',vf,'-loop','0',str(OUT/(name+'.gif'))],check=True)

def hero():
 clip('trustchain-hero',1.5,8,1100)

STYLE='<style>text{font-family:Arial,Helvetica,sans-serif}.title{font-size:35px;font-weight:700;fill:#f5f1e9}.label{font-size:21px;font-weight:700;fill:#f5f1e9}.body{font-size:17px;fill:#c1b29e}.micro{font-size:13px;fill:#b3ef69;letter-spacing:2px}.edge{fill:none;stroke:#b3ef69;stroke-width:2;marker-end:url(#arrow)}.secondary{fill:none;stroke:#dcc8a5;stroke-width:2;stroke-dasharray:6 5;marker-end:url(#purple)}</style>'
def svg_start(h,title,sub):
 return [f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="{h}" viewBox="0 0 1280 {h}" role="img"><title>{html.escape(title)}</title><desc>{html.escape(sub)}</desc>'+STYLE+'<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="#b3ef69"/></marker><marker id="purple" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="#dcc8a5"/></marker></defs>'+f'<rect width="1280" height="{h}" rx="20" fill="{BG}"/>',f'<text x="48" y="63" class="title">{html.escape(title)}</text>',f'<text x="48" y="99" class="body">{html.escape(sub)}</text>']
def card(s,x,y,w,h,tag,title,lines,color=ACCENT):
 s.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{PANEL}" stroke="{LINE}"/><rect x="{x}" y="{y+18}" width="3" height="{h-36}" rx="1" fill="{color}"/><text x="{x+22}" y="{y+29}" class="micro" style="fill:{color}">{html.escape(tag)}</text><text x="{x+22}" y="{y+60}" class="label">{html.escape(title)}</text>')
 for i,line in enumerate(lines):s.append(f'<text x="{x+22}" y="{y+89+i*25}" class="body">{html.escape(line)}</text>')
def path(s,p,purple=False):s.append(f'<path d="{p}" class="'+('secondary' if purple else 'edge')+'"/>')
def diagrams():
 s=svg_start(1080,'One system. Three paths to trust.','Execute a workflow, instrument your own agent, or independently verify recorded evidence.')
 card(s,48,140,360,120,'01 / HUMAN','Next.js workspace',['Runs · scores · evidence · team'])
 card(s,460,140,360,120,'02 / MACHINE','Python + TypeScript SDKs',['Register · instrument · inspect'])
 card(s,872,140,360,120,'03 / VERIFIER','Local + on-chain checks',['Proofs · roots · transaction receipts'],GREEN)
 path(s,'M228 260 V310');path(s,'M640 260 V310');path(s,'M1052 260 V310')
 card(s,48,310,1184,120,'CONTROL PLANE','FastAPI · scoped access · organization / project boundaries',['Cookie sessions + CSRF · API key scopes · authorization · signed run streams · verification endpoints'])
 path(s,'M350 430 V490');path(s,'M825 430 V490');path(s,'M1115 430 V490')
 card(s,48,490,610,140,'EXECUTION','Researcher → Validator → Scorer → Reporter',['LangGraph orchestration · Groq model calls','Tavily search / fact-check tools through MCP'])
 card(s,710,490,230,140,'LIVE SIGNAL','Redis',['SSE event bus','Rate limits'],CHALK)
 card(s,990,490,242,140,'OBSERVATION','Watchdog',['Integrity sweeps','Alert deliveries'],CHALK)
 path(s,'M350 630 V695');path(s,'M825 490 V455 H1260 V370 H1238',True)
 card(s,48,695,610,140,'DURABLE RECORD','PostgreSQL',['steps + anchor_outbox commit together','Runs · batches · tenancy · alerts · read models'])
 path(s,'M658 760 H710');path(s,'M1115 630 V660 H610 V690',True)
 card(s,710,695,522,140,'ASYNC WRITE','Anchor worker',['Claim outbox → Merkle batch → submit root','Signer transaction lock coordinates nonce use'])
 path(s,'M970 835 V900')
 card(s,710,900,522,140,'EVIDENCE CHAIN','V2 Solidity contracts',['Audit roots · trust scores · agent identities','Deployment registry · Anvil locally / Monad target'],GREEN)
 card(s,48,900,610,140,'READ / RECONCILE','Chain indexer',['Events → scores / identities / confirmed evidence','Read models are derived from chain events'])
 path(s,'M710 970 H658');path(s,'M350 900 V840')
 s.append('<path d="M1232 200 H1268 V970 H1238" fill="none" stroke="#92c952" stroke-width="2" marker-end="url(#arrow)"/><path d="M1232 410 H1268" fill="none" stroke="#92c952" stroke-width="2"/><text x="48" y="1064" style="font:13px Arial;fill:#c1b29e">Green: primary evidence path · Chalk: streaming / monitoring · Outer path: direct contract operations</text>')
 s.append('</svg>');(OUT/'architecture.svg').write_text('\n'.join(s))
 s=svg_start(680,'From an action to an independently checked proof.','A durable database write and an on-chain confirmation are separate, visible stages.')
 steps=[('01 / CAPTURE','Hash the step',['Agent + action','Input / output hashes']),('02 / COMMIT','Write atomically',['Step + outbox','One DB transaction']),('03 / BATCH','Build the tree',['Ordered leaves','One Merkle root']),('04 / CONFIRM','Anchor the root',['Async chain write','Receipt + block']),('05 / CHECK','Verify the proof',['Leaf + sibling path','Compare with root'])]
 for i,(tag,title,lines) in enumerate(steps):
  x=48+i*244;card(s,x,162,208,170,tag,title,lines,GREEN if i==4 else ACCENT)
  if i<4:path(s,f'M{x+208} 246 H{x+238}')
 card(s,48,398,560,170,'LOCAL CHECK','Does this proof reconstruct its supplied root?',['Fold the leaf with its sibling hashes.','No network is needed for the calculation.'],CHALK)
 card(s,656,398,576,170,'ON-CHAIN CHECK','Does the chain attest to this evidence?',['Read the configured V2 contract directly.','Check against anchored evidence, not only API data.'],GREEN)
 s.append('<text x="48" y="625" class="body">Integrity proves that evidence matches its commitment. It does not prove an AI answer is factually correct.</text></svg>');(OUT/'evidence-flow.svg').write_text('\n'.join(s))
 s=svg_start(450,'A changed identity becomes an investigation.','Continuous integrity connects agent configuration, recorded activity, and human review.')
 for i,(tag,title,lines) in enumerate([('01 / REGISTER','Bind a fingerprint',['Model + version + prompt','Client-side configuration hash']),('02 / OBSERVE','Compare activity',['SDK includes its fingerprint','Mismatch raises a finding']),('03 / INVESTIGATE','Inspect the evidence',['Expected / presented hashes','Add an investigation note']),('04 / RESOLVE','Close the loop',['Acknowledge / resolve','Reopen with history intact'])]):
  x=48+i*305;card(s,x,160,267,180,tag,title,lines,CHALK if i==1 else ACCENT)
  if i<3:path(s,f'M{x+267} 250 H{x+298}')
 s.append('</svg>');(OUT/'identity-flow.svg').write_text('\n'.join(s))
 # A compact editorial strip, deliberately a dated validation snapshot rather than a CI badge.
 s=svg_start(210,'Verified in the running system.','Redesign validation · November 14, 2026 · latest applicable runs; see the report for scope.')
 for i,(n,label) in enumerate([('30 / 30','BROWSER SCENARIOS'),('20','FRONTEND UNIT TESTS'),('7','SOURCE / REPORT CHECKS'),('5:26','SILENT CINEMATIC TOUR')]):
  x=48+i*305;s.append(f'<text x="{x}" y="153" style="font:700 35px Arial;fill:{ACCENT}">{n}</text><text x="{x}" y="186" class="micro">{label}</text>')
 s.append('</svg>');(OUT/'validation.svg').write_text('\n'.join(s))

def actual_media():
 metadata=json.loads(METADATA.read_text())
 digest=hashlib.sha256(VIDEO.read_bytes()).hexdigest()
 if digest!=metadata['sha256']:raise ValueError('Demo hash does not match its verified metadata')
 shots={'landing':6,'overview':322,'completed-run':111,'trust-scores':125,'audit-trail':140,'proof':174,'agent-identity':234,'fingerprint-mismatch':244,'integrity-alert':284,'notifications':303,'api-keys':315}
 for name,t in shots.items():
  subprocess.run(['ffmpeg','-y','-loglevel','error','-ss',str(t),'-i',str(VIDEO),'-frames:v','1','-vf','scale=1280:-1:flags=lanczos',str(OUT/(name+'.png'))],check=True)
 clips={'run-to-evidence':(110,16),'proof-in-action':(160,17),'identity-to-alert':(241,26)}
 for name,(start,length) in clips.items():clip(name,start,length)
 supporting={}
 for name in ['landing-desktop','landing-pipeline','landing-integrity','mobile-overview','mobile-proofs']:
  source=ROOT/f'artifacts/redesign/screenshots/{name}.png'
  shutil.copy2(source,OUT/(name+'.png'))
  supporting[name]={'source':str(source.relative_to(ROOT)),'data':'explicit API fixtures for responsive layout' if name.startswith('mobile-') else 'actual public landing page; its local SHA-256 demonstration is labeled as an example'}
 provenance={'source':str(VIDEO.relative_to(ROOT)),'sha256':digest,'drive_url':DRIVE,'duration_seconds':float(metadata['format']['duration']),'theme':metadata['theme'],'audio_tracks':metadata['audio_tracks'],'screenshots_seconds':shots,'clip_ranges_seconds':{name:[start,start+length] for name,(start,length) in clips.items()},'hero_clip':{'range_seconds':[1.5,9.5],'width':1100,'fps':8},'supporting_images':supporting,'processing':'Full video frames preserved, including chapter labels and animated field-note overlays. Screenshots downscaled to 1280px; product GIFs to 720px at 8fps. No application data is replaced.','graphics':'Original code-generated SVG diagrams recolored to espresso brown, warm white, and green. The validation strip is a dated snapshot, not a live badge.'}
 (OUT/'media-provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
if __name__=='__main__':
 if not VIDEO.exists():raise FileNotFoundError(VIDEO)
 actual_media();hero();diagrams();print('README media ready:',OUT)
