"""Generate editable SVG architecture figures from the verified Vindex flow.

No application or cloud access. PNG previews are rendered from these SVGs.
"""
from html import escape
from pathlib import Path

OUT=Path(__file__).resolve().parent
NAVY='#315B82'; TEAL='#118C97'; CYAN='#38B3CD'; INK='#19334A'; GRAY='#51687A'


class Figure:
    def __init__(self,w,h,number,title,subtitle):
        self.w,self.h=w,h;self.layers=[[],[],[],[]]
        self.text(55,48,f'VINDEX  /  {number}',15,CYAN,weight=700)
        self.text(55,91,title,34,INK,weight=700)
        self.text(55,124,subtitle,18,GRAY)

    def text(self,x,y,value,size=19,color=INK,anchor='start',weight=400,layer=3):
        self.layers[layer].append(f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-size="{size}" font-weight="{weight}" fill="{color}">{escape(value)}</text>')

    def box(self,x,y,w,h,title,lines=(),accent=CYAN,fill='white',size=22):
        self.layers[2].append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{accent}" stroke-width="2.2"/>')
        total=28+len(lines)*24;top=y+(h-total)/2+22
        self.text(x+w/2,top,title,size,INK,'middle',650,2)
        for i,line in enumerate(lines): self.text(x+w/2,top+27+24*i,line,17,GRAY,'middle',layer=2)

    def group(self,x,y,w,h,label):
        self.layers[0].append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="#F7FBFD" stroke="{CYAN}" stroke-width="1.7" stroke-dasharray="8 7"/>')
        self.text(x+22,y+31,label,18,INK,weight=650,layer=0)

    def edge(self,points,color=NAVY,dashed=False,both=False,width=2):
        marker='teal' if color==TEAL else 'navy'
        attrs=(' stroke-dasharray="7 6"' if dashed else '')+(f' marker-start="url(#{marker})"' if both else '')
        self.layers[1].append(f'<polyline points="{" ".join(f"{x},{y}" for x,y in points)}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round" stroke-linecap="round" marker-end="url(#{marker})"{attrs}/>')

    def label(self,x,y,text,size=16,color=GRAY):
        width=len(text)*size*.53+16
        self.layers[3].append(f'<rect x="{x-width/2}" y="{y-size}" width="{width}" height="{size+8}" rx="4" fill="white"/>')
        self.text(x,y,text,size,color,'middle')

    def footer(self,lines,y=None):
        y=y or self.h-90
        self.layers[3].append(f'<path d="M55 {y} H{self.w-55}" stroke="#D7E6EC"/>')
        for i,line in enumerate(lines): self.text(55,y+28+i*25,line,17,GRAY)

    def save(self,name):
        markers=''.join(f'<marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 Z" fill="{color}"/></marker>' for name,color in [('navy',NAVY),('teal',TEAL)])
        svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}" role="img"><title>Vindex {escape(name)}</title><defs>{markers}</defs><rect width="100%" height="100%" fill="white"/><g font-family="Segoe UI, Arial, sans-serif">'+''.join(''.join(layer) for layer in self.layers)+'</g></svg>\n'
        (OUT/(name+'.svg')).write_text(svg,encoding='utf-8',newline='\n')


def system_flow():
    f=Figure(1600,1050,'01  SYSTEM FLOW','Upload directly to S3; coordinate through the API',
             'Current core path: same media bucket, durable jobs, separate search and playback worker pools.')
    f.group(65,495,445,350,'ONE S3 MEDIA BUCKET  ·  separate prefixes')
    f.group(660,565,330,240,'ECS FARGATE WORKER POOLS')
    f.edge([(290,230),(365,230)])
    f.edge([(625,230),(710,230)])
    f.edge([(990,230),(1120,230)])
    f.edge([(170,280),(170,340)],both=True)
    f.edge([(495,280),(495,340)],both=True)
    f.edge([(60,230),(25,230),(25,605),(95,605)],TEAL,width=2.8)
    f.edge([(270,605),(300,605)],TEAL,width=2.8)
    f.edge([(850,280),(850,455),(530,455),(530,580),(480,580)],TEAL)
    f.edge([(1300,280),(1300,410)])
    f.edge([(1300,505),(1300,580)])
    f.edge([(1120,625),(1040,625),(1040,650),(965,650)])
    f.edge([(1040,650),(1040,745),(965,745)])
    f.edge([(480,605),(575,605),(575,650),(685,650)],TEAL)
    f.edge([(575,650),(575,745),(685,745)],TEAL)
    f.edge([(825,805),(825,870),(290,870),(290,800)],TEAL)
    f.edge([(965,670),(1010,670),(1010,835),(1120,835)],TEAL)
    f.edge([(990,585),(1005,585),(1005,335),(1540,335),(1540,230),(1480,230)],NAVY,dashed=True)
    f.box(60,180,230,100,'User / browser',['React app'])
    f.box(365,180,260,100,'CloudFront + API GW',['UI delivery + API routing'],size=20)
    f.box(710,180,280,100,'FastAPI on Fargate',['Authorize • intent • complete'])
    f.box(1120,180,360,100,'Postgres',['Video + reservation + two jobs'])
    f.box(60,340,230,80,'Cognito',['Sign-in and JWTs'],size=21)
    f.box(365,340,260,80,'Frontend S3 bucket',['React assets'],size=21)
    f.box(95,555,175,100,'Upload key',['Browser-writable'],size=21)
    f.box(300,555,180,100,'Accepted raw',['Server-only copy'],size=21)
    f.box(95,710,385,90,'Published artifacts',['MP4 • thumbnail • audio • transcript'])
    f.box(1120,410,360,95,'Durable-job dispatcher',['Poll + recover + publish'])
    f.box(1120,580,360,90,'Redis / Celery',['Transport for task messages'])
    f.box(685,615,280,70,'Search workers',['Whisper + embeddings'],size=21)
    f.box(685,710,280,70,'Playback workers',['FFmpeg + thumbnail'],size=21)
    f.box(1120,790,360,90,'Qdrant',['Timestamped segment vectors'])
    f.label(495,163,'1. intent / 3. complete / status')
    f.label(1055,215,'Atomic commit',15)
    f.label(154,472,'2. direct video POST',17,TEAL)
    f.label(685,444,'3. freeze + verify input',17,TEAL)
    f.label(1300,365,'4. poll committed work')
    f.label(1300,551,'5. publish task IDs')
    f.label(572,690,'Read raw',15,TEAL)
    f.label(590,893,'6. publish outputs',17,TEAL)
    f.label(1270,321,'Commit status + artifact pointers',16)
    f.text(60,935,'Read path',18,INK,weight=650)
    f.text(166,935,'API authorizes playback → browser uses a signed S3 GET. Semantic queries go through API → Qdrant.',18,GRAY)
    f.footer(['Blue = control / state. Teal = media / artifacts. VPC link + Cloud Map connect API Gateway to API tasks.',
              'CloudFront currently fronts the app and API; the media bucket is not configured as a CloudFront origin.'],965)
    f.save('01-system-flow')


def scheduler():
    f=Figure(1800,970,'02  ORCHESTRATION','Durable job state in Postgres; worker capacity in ECS',
             'Vindex implements the scheduler in durable job rows and a dispatcher, with scaling alongside the task flow.')
    f.edge([(295,445),(350,445)])
    f.edge([(625,445),(690,445)])
    f.edge([(950,445),(1020,445)])
    f.edge([(1245,445),(1320,445)])
    f.edge([(1495,385),(1495,265),(487,265),(487,385)])
    f.edge([(487,505),(487,570),(305,570),(305,710),(350,710)],dashed=True)
    f.edge([(625,710),(735,710)],dashed=True)
    f.edge([(1095,710),(1210,710),(1210,570),(1450,570),(1450,505)],dashed=True)
    f.edge([(1590,505),(1590,660)],TEAL,both=True)
    f.box(55,385,240,120,'Upload accepted',['Video + initial jobs','in one DB transaction'])
    f.box(350,385,275,120,'Postgres job state',['PipelineJob + chunks','Dependencies, leases, retries'])
    f.box(690,385,260,120,'Dispatcher',['Poll eligible work','Recover expired leases'])
    f.box(1020,385,225,120,'Redis queues',['search / playback','Celery delivery'])
    f.box(1320,385,350,120,'Task workers',['Separate search / playback services','Claim → execute → publish'])
    f.box(350,660,275,100,'Backlog metrics',['Read durable jobs in Postgres'])
    f.box(735,660,360,100,'CloudWatch → ECS scaling',['Wake, grow, return idle pools to zero'])
    f.box(1320,660,350,100,'Artifact storage',['S3: attempt-specific object keys','Worker disk: temporary files'])
    f.label(988,249,'Workers claim, heartbeat and commit with a valid lease + attempt token',18)
    f.label(657,430,'Poll',15)
    f.label(987,430,'Publish',15)
    f.label(1281,430,'Deliver',15)
    f.label(420,557,'Measure backlog',16)
    f.label(1210,620,'Change task count',16)
    f.label(1590,592,'Read / write',17,TEAL)
    f.group(335,610,780,180,'CAPACITY CONTROL  ·  outside the job dependency chain')
    f.text(55,835,'Recovery:',19,INK,weight=650)
    f.text(160,835,'Unclaimed jobs can be delivered again; stale attempts cannot publish authoritative results.',19,GRAY)
    f.footer(['The current state tier consolidates Postgres, Redis, Qdrant, dispatcher, maintenance and metrics on one EC2 host.',
              'There is no separate generic DAG engine, completion queue, or inline resource-manager service in this path.'],885)
    f.save('02-orchestration')


def dag():
    f=Figure(1800,1120,'03  PROCESSING DAG','One accepted video → independent playback and search',
             'Only true dependencies are ordered. Worker capacity determines how much of the available fan-out runs at once.')
    f.group(360,180,1030,250,'PLAYBACK JOB  ·  one task; the steps below remain sequential')
    f.group(360,495,1030,390,'SEARCH BRANCH  ·  prepare → chunk jobs → finalize')
    f.edge([(275,480),(320,480),(320,305),(395,305)])
    f.edge([(320,480),(320,685),(395,685)])
    f.edge([(625,305),(675,305)])
    f.edge([(945,305),(995,305)])
    f.edge([(1355,305),(1450,305)])
    f.edge([(625,685),(655,685),(655,585),(685,585)])
    f.edge([(655,685),(685,685)])
    f.edge([(655,685),(655,785),(685,785)])
    f.edge([(955,585),(985,585),(985,685),(1035,685)])
    f.edge([(955,685),(985,685)])
    f.edge([(955,785),(985,785),(985,685)])
    f.edge([(1355,685),(1450,685)])
    f.edge([(1575,735),(1575,935)])
    f.edge([(1700,305),(1740,305),(1740,980),(1700,980)])
    f.box(55,420,220,120,'Accepted raw video',['Frozen S3 input','Two durable initial jobs'])
    f.box(395,260,230,90,'FFmpeg transcode',['360p MP4'])
    f.box(675,260,270,90,'Generate thumbnail',['Generation is best effort'])
    f.box(995,260,360,90,'Publish playback',['S3 objects + guarded DB pointers'])
    f.box(1450,260,250,90,'Playable',['Signed S3 media URL'],accent=TEAL,fill='#ECF9F7')
    f.box(395,630,230,110,'Prepare search',['Probe duration, extract audio','Commit chunk manifest'],size=21)
    for y,name in [(540,'Chunk 1'),(640,'Chunk 2'),(740,'Chunk N')]:
        f.box(685,y,270,90,name,['Whisper → embeddings','Qdrant + S3; guarded commit'],size=20)
    f.box(1035,630,320,110,'Finalize search',['Wait for all chunks','Merge final artifacts + DB state'])
    f.box(1450,640,250,95,'Searchable',['API queries Qdrant'],accent=TEAL,fill='#ECF9F7')
    f.box(1450,935,250,90,'Video: processed',['Both milestones complete'],accent=INK,fill='#F1F5F8',size=22)
    f.label(510,784,'Default: 300s chunks',16)
    f.label(510,810,'5s context overlap',16)
    f.text(395,400,'Playback does not wait for transcription.',19,TEAL,weight=650)
    f.text(395,857,'Each chunk does ASR → embeddings → publication; chunks can run independently.',18,GRAY)
    f.text(55,962,'Two readiness milestones',22,INK,weight=650)
    f.text(55,994,'Search and playback can become usable at different times.',19,GRAY)
    f.footer(['Current search API waits for finalization; one published chunk alone does not enable video search.',
              'No watermark task, adaptive-bitrate ladder, or separate audio/video assembly stage is implemented in this core path.'],1030)
    f.save('03-processing-dag')


if __name__=='__main__':
    system_flow();scheduler();dag()
