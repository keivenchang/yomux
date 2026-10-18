// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/capabilities"
	"github.com/keivenchang/yomux/internal/stats"
	"github.com/keivenchang/yomux/internal/testsupport"
)

// This fixture exercises the product HTML, Go routes, shared browser model and
// rendered tab while the actual metadata builder is held behind a channel.
func TestAgentWindowMetadataRefreshBrowser(t *testing.T) {
	root := testsupport.RepoRoot(t)
	private, err := testsupport.MkdirShortTemp("yw-")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(private)
	socket := filepath.Join(private, "tmux.sock")
	t.Setenv("YOMUX_TMUX_SOCKET", socket)
	t.Setenv("YOMUX_CONFIG_DIR", private)
	t.Setenv("YOMUX_STATE_DIR", private)
	command := exec.Command("tmux", "-S", socket, "new-session", "-d", "-s", "working", "-x", "120", "-y", "40", "printf '\342\227\246 Working (4m 40s \342\200\242 esc to interrupt)\\n'; exec tail -f /dev/null")
	if out, err := command.CombinedOutput(); err != nil {
		t.Fatalf("private tmux: %v %s", err, out)
	}
	defer exec.Command("tmux", "-S", socket, "kill-server").Run()
	paneBytes, err := exec.Command("tmux", "-S", socket, "display-message", "-p", "-t", "working:0", "#{pane_id}").Output()
	if err != nil {
		t.Fatal(err)
	}
	pane := strings.TrimSpace(string(paneBytes))
	store, err := newGoYoloStore(private)
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	service := stats.NewStatsService(nil, stats.StatsServiceConfig{AgentStatus: func(ctx context.Context) (stats.AgentStatusSnapshot, error) {
		classified := stats.ClassifyAgentStatus(ctx, stats.AgentStatusRequest{Kind: "codex", PID: 1, PaneTarget: pane}, stats.AgentStatusSources{
			Process: func(context.Context, int) (stats.AgentStatusProcess, error) {
				return stats.AgentStatusProcess{Alive: true}, nil
			},
			CapturePane: func(ctx context.Context, target string) (stats.AgentStatusPane, error) {
				capture, err := exec.CommandContext(ctx, "tmux", "-S", socket, "capture-pane", "-p", "-t", target).Output()
				return stats.AgentStatusPane{Target: target, VisibleText: string(capture)}, err
			},
		})
		return stats.AgentStatusSnapshot{Available: true, SourceGeneration: 23,
			Freshness: &stats.AgentStatusFreshness{ObservedAt: float64(time.Now().UnixNano()) / 1e9},
			Agents:    []any{map[string]any{"session": "working", "pane": pane, "kind": "codex", "state": string(classified.Status), "process_instance_id": "fixture:codex:1"}},
		}, nil
	}})
	s := &server{root: root, config: private, yolo: store, statsService: service, capabilities: capabilities.New("fixture"), events: newEventBroker(),
		tmuxWindowActivityFn: func() (map[string]float64, error) { return map[string]float64{}, nil }}
	s.capabilities.Set("yolo.auto-approve.v1", capabilities.Entry{Available: true})
	info := func() map[string]map[string]any {
		return map[string]map[string]any{"working": {
			"panes":  []map[string]any{{"target": pane, "pane_id": pane, "window_index": 0, "window_active": true, "active": true, "command": "codex", "window_name": "codex"}},
			"agents": []map[string]any{{"window_index": 0, "pane_target": pane, "kind": "codex", "status": "working", "status_observed_at": float64(time.Now().UnixNano()) / 1e9, "process_instance_id": "fixture:codex:1", "current": true, "window_active": true}},
		}}
	}
	started, release := make(chan struct{}), make(chan struct{})
	var startedOnce, releaseOnce sync.Once
	defer releaseOnce.Do(func() { close(release) })
	s.metadataEnrichmentFn = func([]string) (map[string]map[string]any, error) {
		startedOnce.Do(func() { close(started) })
		<-release
		return info(), nil
	}
	topology, err := s.tmuxTopologySnapshot()
	if err != nil {
		t.Fatal(err)
	}
	initial := info()["working"]
	initial["session_identity"] = goTmuxSessionIdentity("working", topology.identities["working"])
	s.metadataPayload = map[string]any{"sessions": map[string]any{"working": initial}, "session_order": []string{"working"}, "topology_generation": topology.generation, "metadata_generation": uint64(1)}
	s.metadataGeneration, s.metadataBuiltAt = 1, time.Now()
	t.Cleanup(s.closeNativePages)
	html, err := s.renderNativeHome(httptest.NewRequest(http.MethodGet, "/", nil), authUser{username: "fixture", role: "admin"}, topology)
	if err != nil {
		t.Fatal(err)
	}
	mux := http.NewServeMux()
	mux.Handle("/static/", http.StripPrefix("/static/", http.FileServer(http.Dir(filepath.Join(root, "static")))))
	mux.HandleFunc("/api/activity", s.activity)
	mux.HandleFunc("/api/auto-approve", s.autoApprove)
	mux.HandleFunc("/api/session-metadata", s.sessionMetadata)
	mux.HandleFunc("/fixture/expire", func(w http.ResponseWriter, r *http.Request) {
		s.metadataMu.Lock()
		s.metadataBuiltAt = time.Now().Add(-3 * goMetadataCacheTTL)
		s.metadataMu.Unlock()
		s.queueMetadataRefresh(false, topology.sessions, topology.identities, nil, topology.generation)
		<-started
		jsonResponse(w, 200, map[string]any{"pending": true})
	})
	mux.HandleFunc("/fixture/release", func(w http.ResponseWriter, r *http.Request) {
		events, unsubscribe := s.events.subscribe("transcripts")
		defer unsubscribe()
		releaseOnce.Do(func() { close(release) })
		select {
		case <-events:
		case <-r.Context().Done():
			return
		}
		jsonResponse(w, 200, map[string]any{"released": true})
	})
	mux.HandleFunc("/api/", func(w http.ResponseWriter, r *http.Request) { jsonResponse(w, 200, map[string]any{}) })
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		_, _ = w.Write(html)
	})
	httpServer := httptest.NewServer(mux)
	defer httpServer.Close()
	// Every projection reads a newly published collector sample, independently of
	// metadata expiry. No product collection timer or sleep controls this test.
	mux.HandleFunc("/fixture/sample", func(w http.ResponseWriter, r *http.Request) {
		if err := service.RefreshAgentStatus(r.Context()); err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		snapshot, _ := s.currentAgentStatusSnapshot(r.Context())
		jsonResponse(w, 200, map[string]any{"sampled": true, "snapshot": snapshot})
	})
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	python := exec.CommandContext(ctx, "python3", "-c", workingBrowserScript, httpServer.URL, private)
	python.Dir = root
	pythonPath := root
	if inheritedPythonPath := os.Getenv("PYTHONPATH"); inheritedPythonPath != "" {
		pythonPath += string(os.PathListSeparator) + inheritedPythonPath
	}
	python.Env = testsupport.SyntheticEnvironment("HOME="+private, "TMPDIR="+private, "PYTHONPATH="+pythonPath)
	output, err := python.CombinedOutput()
	if err != nil {
		t.Fatalf("browser transition: %v\n%s", err, output)
	}
	t.Log(string(output))
}

const workingBrowserScript = `
import json, os, sys
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
options=Options()
options.binary_location=os.environ['CHROME_BIN']
for flag in ('--headless=new','--no-sandbox','--disable-dev-shm-usage','--window-size=1400,900'):
 options.add_argument(flag)
options.add_argument('--user-data-dir='+sys.argv[2]+'/chrome')
driver=webdriver.Chrome(service=Service(os.environ['CHROMEDRIVER_BIN']),options=options)
try:
 driver.get(sys.argv[1]+'/?tabs=working&layout=1')
 WebDriverWait(driver,15).until(lambda d:d.execute_script("return typeof applyAutoApprovePayload==='function' && typeof sessionAgentWindowStatusModel==='function' && document.querySelector('.pane-tab')!==null"))
 trace=[]
 failures=[]
 for phase in ('warm','refresh-pending','complete'):
  result=driver.execute_async_script('''
   const phase=arguments[0], done=arguments[arguments.length-1];
   (async()=>{
    if(phase==='refresh-pending') await fetch('/fixture/expire');
    const collector=await (await fetch('/fixture/sample')).json();
    if(phase==='complete') await fetch('/fixture/release');
    const activity=await (await fetch('/api/activity')).json();
    const auto=await (await fetch('/api/auto-approve')).json();
    await fetchTabberActivity({visible:true,allowHidden:true});
    const refresh=await loadAutoStatuses({force:true,render:false});
    renderSessionButtons({force:true});
    renderAutoApproveStatusSurfaces({changedSessions:['working']});
    const model=sessionAgentWindowStatusModel('working');
    const tab=document.querySelector('.file-tree-row[data-tabber-type="normal"][data-tabber-session="working"]');
    const dot=tab?.querySelector('.agent-window-status-dot.status-indicator--working');
    const animation=dot?.getAnimations().find(a=>a.animationName==='agent-status-opacity-pulse');
    let paint=[];
    if(animation){animation.pause();const duration=animation.effect.getTiming().duration;for(const fraction of [0,0.25,0.5,0.75]){animation.currentTime=duration*fraction;paint.push(getComputedStyle(dot).opacity);}}
    return {phase,collector,activity,auto,model,refresh,dom:tab?.outerHTML||'',animation:animation?.animationName,paint,tabberOpen:!!document.querySelector('#panel-__tabber__')};
   })().then(done,error=>done({error:String(error),stack:error.stack}));
  ''',phase)
  trace.append(result)
  if 'error' in result:
   failures.append((phase,result)); continue
  rows=result['auto']['sessions']['working']['agent_windows']
  model=result['model']['agents']
  if not rows or rows[0].get('state')!='working': failures.append((phase,'canonical',rows))
  if not model or model[0].get('state')!='working': failures.append((phase,'model',model))
  if result.get('animation')!='agent-status-opacity-pulse' or len(set(result['paint']))<2: failures.append((phase,'pulse',result.get('animation'),result['paint']))
  if result['auto']['sessions']['working']['enabled'] or not result['tabberOpen']: failures.append((phase,'layout'))
  if phase=='refresh-pending' and not (result['activity']['cache']['stale'] and result['activity']['cache']['refreshing']): failures.append((phase,'barrier'))
  if phase=='complete' and (result['activity']['cache']['stale'] or result['activity']['cache']['generation']<=1): failures.append((phase,'publication'))
 print(json.dumps(trace,sort_keys=True))
 assert not failures,failures
finally:
 driver.quit()
`
