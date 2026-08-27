"""Split WAL vs checkpoint cost, and probe pragma sensitivity."""
import json, os, sys, time
sys.path.insert(0, "/home/keivenc/dev/yolomux.release0715")
from yolomux_lib.stats_current import storage
import yolomux_lib.stats_current.resolution as res

os.makedirs("/tmp/yolomux-e3-remeasure-08/db/ref3", exist_ok=True)
DB = "/tmp/yolomux-e3-remeasure-08/db/ref3/stats-v8.sqlite3"
def iow():
    for l in open("/proc/self/io"):
        if l.startswith("write_bytes:"): return int(l.split()[1])

def run(label, autockpt=None, synchronous=None, seconds=1800, families=2):
    for s in ("", "-wal", "-shm"):
        try: os.unlink(DB+s)
        except FileNotFoundError: pass
    st = storage.Store.open(DB, writer_protocol=storage.MIN_WRITER_PROTOCOL, writer_build=storage.MIN_WRITER_BUILD)
    st.initialize_ring_storage()
    conn = st._connection()
    if autockpt is not None: conn.execute(f"PRAGMA wal_autocheckpoint = {autockpt}")
    if synchronous is not None: conn.execute(f"PRAGMA synchronous = {synchronous}")
    t0 = 1_750_000_000.0
    writes=[]
    for r, slots in res.RING_CAPACITIES.items():
        for i in range(slots):
            start=(int(t0)//r)*r-(slots-1-i)*r
            if start>=0: writes.append(storage.RingBucketWrite(r,start,json.dumps({"series":{"system_cpu_percent":1.0}}),True))
    st.publish_ring_buckets(buckets=tuple(writes), source_generation=1, published_at=t0)
    eps=[f"e{i}" for i in range(families)]
    for i,e in enumerate(eps):
        st.append_batch(observations=(storage.Observation(f"w{i}","cpu",f"s{i}",t0,e,0,{"process_percent":1.0,"system_percent":2.0}),),
                        coverage_epochs=(storage.CoverageEpoch("cpu",f"s{i}",e,t0,t0,1.0,0),))
    os.sync(); i0=iow(); m0=os.path.getsize(DB)
    for n in range(seconds):
        at=t0+n+1
        for i,e in enumerate(eps):
            st.append_batch(observations=(storage.Observation(f"o{i}-{n}","cpu",f"s{i}",at,e,0,{"process_percent":1.0+n%7,"system_percent":2.0}),),
                            coverage_epochs=(storage.CoverageEpoch("cpu",f"s{i}",e,t0,at,1.0,0),))
    wal=os.path.getsize(DB+"-wal") if os.path.exists(DB+"-wal") else 0
    os.sync(); i1=iow(); m1=os.path.getsize(DB)
    st.close()
    commits=seconds*families
    print(f"{label:46s} commits={commits:6d} io={((i1-i0)/1e6):7.2f}MB per_commit={(i1-i0)/commits:8.0f}B "
          f"day={(i1-i0)/seconds*86400/1e9:5.2f}GB  final_wal={wal/1e6:6.2f}MB main {m0/1e6:.2f}->{m1/1e6:.2f}MB")

run("default (autockpt=1000, sync=NORMAL)")
run("no checkpoint during run (autockpt=0)", autockpt=0)
run("autockpt=4000", autockpt=4000)
run("synchronous=OFF", synchronous="OFF")
