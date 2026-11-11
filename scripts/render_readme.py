"""Build README motion graphics and extract authentic product media from the demo."""
from pathlib import Path
import math, subprocess, json, html
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'assets/readme'; OUT.mkdir(parents=True,exist_ok=True)
VIDEO=ROOT/'artifacts/demo/q.mp4'
FONT='/usr/share/fonts/Adwaita/AdwaitaSans-Regular.ttf'
BOLD='/usr/share/fonts/ttf-readex-pro/ReadexPro-bold.ttf'
MONO='/usr/share/fonts/Adwaita/AdwaitaMono-Regular.ttf'
def font(n,b=False,mono=False):return ImageFont.truetype(MONO if mono else BOLD if b else FONT,n)
BG='#080e20'; PANEL='#101b32'; LINE='#263853'; INK='#eef4ff'; MUTED='#a4b5d0'; CYAN='#40e8ed'; PURPLE='#a995ff'; GREEN='#72efb0'

def hero():
 frames=[]; W,H=1200,510
 for k in range(72):
  t=k/12; phase=min(3,int(t/1.5)); im=Image.new('RGB',(W,H),BG);d=ImageDraw.Draw(im)
  for x in range(0,W,40):d.line((x,0,x,H),fill='#101a2c')
  for y in range(0,H,40):d.line((0,y,W,y),fill='#101a2c')
  d.rectangle((0,0,690,H),fill=BG)
  d.text((48,30),'T R U S T C H A I N',font=font(19,True),fill=CYAN)
  d.text((48,87),'Every agent action.',font=font(49,True),fill=INK)
  d.text((48,149),'A trail you can verify.',font=font(49,True),fill=INK)
  d.text((50,229),'Run agents. Bind identity. Anchor evidence.',font=font(22),fill=MUTED)
  d.text((50,262),'Independent verification starts here.',font=font(22),fill=MUTED)
  d.rounded_rectangle((50,320,460,354),radius=17,fill=PANEL,outline=LINE)
  d.text((68,329),'DURABLE STEPS  /  MERKLE PROOFS  /  LIVE ALERTS',font=font(11,mono=True),fill=CYAN)
  # A moving signal builds a tree into one independently checked root.
  leaves=[(755,115),(755,180),(755,245),(755,310)]; mids=[(900,150),(900,280)]; root=(1050,215)
  for i,a in enumerate(leaves):
   b=mids[i//2]; d.line((a,b),fill=LINE,width=3)
   if i<=phase:
    q=(t*.9+i*.23)%1; p=(a[0]+(b[0]-a[0])*q,a[1]+(b[1]-a[1])*q);d.ellipse((p[0]-4,p[1]-4,p[0]+4,p[1]+4),fill=CYAN)
  for i,a in enumerate(mids):
   d.line((a,root),fill=LINE,width=3);q=(t*.8+i*.4)%1;p=(a[0]+(root[0]-a[0])*q,a[1]+(root[1]-a[1])*q);d.ellipse((p[0]-5,p[1]-5,p[0]+5,p[1]+5),fill=PURPLE)
  for i,(x,y) in enumerate(leaves):
   color=CYAN if i<=phase else LINE; d.rounded_rectangle((x-26,y-17,x+26,y+17),radius=7,fill=PANEL,outline=color,width=2);d.text((x-14,y-7),'0x'+str(i+1),font=font(11,mono=True),fill=INK)
  for x,y in mids:d.rounded_rectangle((x-23,y-18,x+23,y+18),radius=7,fill=PANEL,outline=PURPLE,width=2)
  x,y=root;d.polygon([(x,y-53),(x+45,y-32),(x+39,y+23),(x,y+55),(x-39,y+23),(x-45,y-32)],fill=PANEL,outline=GREEN,width=3);d.line([(x-19,y),(x-4,y+15),(x+23,y-17)],fill=GREEN,width=5)
  d.text((981,298),'ONE ROOT.',font=font(15,True),fill=GREEN);d.text((956,323),'CHECK IT YOURSELF.',font=font(12,mono=True),fill=MUTED)
  names=['01  RECORD','02  FINGERPRINT','03  ANCHOR','04  VERIFY']
  for i,name in enumerate(names):
   x=48+i*288;active=i==phase;d.rounded_rectangle((x,410,x+266,470),radius=10,fill=PANEL,outline=CYAN if active else LINE,width=2 if active else 1);d.text((x+18,431),name,font=font(15,mono=True),fill=CYAN if active else MUTED)
  d.rectangle((48,491,48+int(1100*k/71),494),fill=CYAN)
  frames.append(im.quantize(colors=96,dither=Image.Dither.NONE))
 frames[0].save(OUT/'trustchain-hero.gif',save_all=True,append_images=frames[1:],duration=83,loop=0,optimize=True,disposal=2)

STYLE='<style>text{font-family:Arial,Helvetica,sans-serif}.title{font-size:35px;font-weight:700;fill:#eef4ff}.label{font-size:21px;font-weight:700;fill:#eef4ff}.body{font-size:17px;fill:#a4b5d0}.micro{font-size:13px;fill:#40e8ed;letter-spacing:2px}.edge{fill:none;stroke:#40e8ed;stroke-width:2;marker-end:url(#arrow)}.secondary{fill:none;stroke:#a995ff;stroke-width:2;stroke-dasharray:6 5;marker-end:url(#purple)}</style>'
def svg_start(h,title,sub):
 return [f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="{h}" viewBox="0 0 1280 {h}" role="img"><title>{html.escape(title)}</title><desc>{html.escape(sub)}</desc>'+STYLE+'<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="#40e8ed"/></marker><marker id="purple" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="#a995ff"/></marker></defs>'+f'<rect width="1280" height="{h}" rx="20" fill="{BG}"/>',f'<text x="48" y="63" class="title">{html.escape(title)}</text>',f'<text x="48" y="99" class="body">{html.escape(sub)}</text>']
def card(s,x,y,w,h,tag,title,lines,color=CYAN):
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
 card(s,710,490,230,140,'LIVE SIGNAL','Redis',['SSE event bus','Rate limits'],PURPLE)
 card(s,990,490,242,140,'OBSERVATION','Watchdog',['Integrity sweeps','Alert deliveries'],PURPLE)
 path(s,'M350 630 V695');path(s,'M825 490 V455 H1260 V370 H1238',True)
 card(s,48,695,610,140,'DURABLE RECORD','PostgreSQL',['steps + anchor_outbox commit together','Runs · batches · tenancy · alerts · read models'])
 path(s,'M658 760 H710');path(s,'M1115 630 V660 H610 V690',True)
 card(s,710,695,522,140,'ASYNC WRITE','Anchor worker',['Claim outbox → Merkle batch → submit root','Signer transaction lock coordinates nonce use'])
 path(s,'M970 835 V900')
 card(s,710,900,522,140,'EVIDENCE CHAIN','V2 Solidity contracts',['Audit roots · trust scores · agent identities','Deployment registry · Anvil locally / Monad target'],GREEN)
 card(s,48,900,610,140,'READ / RECONCILE','Chain indexer',['Events → scores / identities / confirmed evidence','Read models are derived from chain events'])
 path(s,'M710 970 H658');path(s,'M350 900 V840')
 s.append('<path d="M1232 200 H1268 V970 H1238" fill="none" stroke="#72efb0" stroke-width="2" marker-end="url(#arrow)"/><path d="M1232 410 H1268" fill="none" stroke="#72efb0" stroke-width="2"/><text x="48" y="1064" style="font:13px Arial;fill:#a4b5d0">Cyan: primary evidence path · Purple: streaming / monitoring · Green outer path: direct V2 contract operations</text>')
 s.append('</svg>');(OUT/'architecture.svg').write_text('\n'.join(s))
 s=svg_start(680,'From an action to an independently checked proof.','A durable database write and an on-chain confirmation are separate, visible stages.')
 steps=[('01 / CAPTURE','Hash the step',['Agent + action','Input / output hashes']),('02 / COMMIT','Write atomically',['Step + outbox','One DB transaction']),('03 / BATCH','Build the tree',['Ordered leaves','One Merkle root']),('04 / CONFIRM','Anchor the root',['Async chain write','Receipt + block']),('05 / CHECK','Verify the proof',['Leaf + sibling path','Compare with root'])]
 for i,(tag,title,lines) in enumerate(steps):
  x=48+i*244;card(s,x,162,208,170,tag,title,lines,GREEN if i==4 else CYAN)
  if i<4:path(s,f'M{x+208} 246 H{x+238}')
 card(s,48,398,560,170,'LOCAL CHECK','Does this proof reconstruct its supplied root?',['Fold the leaf with its sibling hashes.','No network is needed for the calculation.'],PURPLE)
 card(s,656,398,576,170,'ON-CHAIN CHECK','Does the chain attest to this evidence?',['Read the configured V2 contract directly.','Check against anchored evidence, not only API data.'],GREEN)
 s.append('<text x="48" y="625" class="body">Integrity proves that evidence matches its commitment. It does not prove an AI answer is factually correct.</text></svg>');(OUT/'evidence-flow.svg').write_text('\n'.join(s))
 s=svg_start(450,'A changed identity becomes an investigation.','Continuous integrity connects agent configuration, recorded activity, and human review.')
 for i,(tag,title,lines) in enumerate([('01 / REGISTER','Bind a fingerprint',['Model + version + prompt','Client-side configuration hash']),('02 / OBSERVE','Compare activity',['SDK includes its fingerprint','Mismatch raises a finding']),('03 / INVESTIGATE','Inspect the evidence',['Expected / presented hashes','Add an investigation note']),('04 / RESOLVE','Close the loop',['Acknowledge / resolve','Reopen with history intact'])]):
  x=48+i*305;card(s,x,160,267,180,tag,title,lines,PURPLE if i==1 else CYAN)
  if i<3:path(s,f'M{x+267} 250 H{x+298}')
 s.append('</svg>');(OUT/'identity-flow.svg').write_text('\n'.join(s))
 # A compact editorial strip, deliberately a dated validation snapshot rather than a CI badge.
 s=svg_start(210,'Verified in the running system.','Local validation snapshot · November 11, 2026 · see the linked report for scope and skips.')
 for i,(n,label) in enumerate([('24','BROWSER SCENARIOS'),('411','BACKEND TESTS'),('49 + 31','PYTHON / TS SDK + CLI'),('0','NPM AUDIT FINDINGS')]):
  x=48+i*305;s.append(f'<text x="{x}" y="153" style="font:700 35px Arial;fill:{CYAN}">{n}</text><text x="{x}" y="186" class="micro">{label}</text>')
 s.append('</svg>');(OUT/'validation.svg').write_text('\n'.join(s))

def actual_media():
 if not VIDEO.exists():raise FileNotFoundError(VIDEO)
 shots={'overview':255,'completed-run':114,'trust-scores':133,'audit-trail':142,'proof':157,'agent-identity':198,'integrity-alert':214,'notifications':237}
 for name,t in shots.items():subprocess.run(['ffmpeg','-y','-loglevel','error','-ss',str(t),'-i',str(VIDEO),'-frames:v','1','-vf','crop=1600:900:0:0',str(OUT/(name+'.png'))],check=True)
 # These are short excerpts of the actual recording; the whole narrated MP4 remains linked.
 for name,t,length in [('run-to-evidence',122,9),('proof-in-action',154,9),('identity-to-alert',202,15)]:
  vf='crop=1600:900:0:0,fps=8,scale=720:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4'
  subprocess.run(['ffmpeg','-y','-loglevel','error','-ss',str(t),'-t',str(length),'-i',str(VIDEO),'-filter_complex',vf,'-loop','0',str(OUT/(name+'.gif'))],check=True)
 (OUT/'media-provenance.json').write_text(json.dumps({'source':'artifacts/demo/q.mp4','sha256':json.loads((ROOT/'artifacts/demo/video-metadata.json').read_text())['sha256'],'screenshots_seconds':shots,'clip_ranges_seconds':{'run-to-evidence':[122,131],'proof-in-action':[154,163],'identity-to-alert':[202,217]},'processing':'Top 1600x900 crop removes recording chapter overlay; GIF previews downscaled to 720px at 8fps. Product content is not replaced.'},indent=2)+'\n')
if __name__=='__main__':
 hero();diagrams();actual_media();print('README media ready:',OUT)
