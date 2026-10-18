// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"context"
	"embed"
	"encoding/json"
	"fmt"
	"html"
	"html/template"
	"net/http"
	"os"
	"os/user"
	"path/filepath"
	"strings"

	"github.com/keivenchang/yomux/internal/finder"
	"github.com/keivenchang/yomux/internal/yoagent"
)

//go:embed pages/*.gohtml
var nativePageTemplates embed.FS

type nativePageView struct {
	ctx                                                              context.Context
	Locale, Direction, Next, Error, AppearanceClass, AuthPath, Login string
	BootstrapJSON                                                    template.JS
	ToggleLabels                                                     map[string]string
	SetupStrings                                                     map[string]string
	SecurityHTML, AgentNoticeHTML                                    template.HTML
	locales                                                          *pageLocales
	version                                                          pageVersion
	server                                                           *server
}

func (s *server) staticAssetRevision(asset string) string {
	info, err := os.Stat(filepath.Join(s.root, "static", asset))
	if err != nil {
		return "0-0"
	}
	return fmt.Sprintf("%d-%d", info.ModTime().UnixNano(), info.Size())
}

func (s *server) staticAssetURL(asset string) string {
	return "/static/" + asset + "?v=" + s.staticAssetRevision(asset)
}

func (view *nativePageView) brand(class, tag string) template.HTML {
	if tag != "div" {
		tag = "span"
	}
	t := view.locales.text
	v := view.version
	backend := goRuntimeLabel()
	title := v.Status + " [" + backend + "]"
	details := title + "\n" + strings.Join(v.Commits, "\n")
	if len(v.Commits) > 0 {
		details += "\n"
	}
	details += t("menu.help.about.sha", "sha", v.SHA) + "\n" + t("menu.help.lastCommit", "time", v.TimePT)
	if v.Count > 0 {
		details += "\n" + t("menu.help.about.commits", "count", fmt.Sprint(v.Count))
	}
	e := html.EscapeString
	// Only fixed tags and markup are trusted. Every translated or runtime value
	// is escaped before this fragment enters a surrounding html/template.
	return template.HTML(fmt.Sprintf(`<%s class="%s" title="%s" aria-label="%s"><span class="brand-mark">%s</span><span class="brand-second brand-green">%s</span><span class="brand-blue">m</span><span class="brand-red">u</span><span class="brand-yellow">x</span><span class="brand-version" title="%s">%s</span><button type="button" class="brand-update-badge" data-update-badge hidden title="%s" aria-label="%s">%s</button></%s>`, tag, e(class), e(title), e(t("brand.version", "version", version)), e(t("brand.wordmark.initial")), e(t("brand.wordmark.second")), e(details), e(v.Status), e(t("update.badgeTitle")), e(t("update.badgeAria")), e(t("update.badgeLabel")), tag))
}

func (view *nativePageView) localePicker(class string) template.HTML {
	e := html.EscapeString
	selected := view.locales.preference
	if selected == "en-XA" {
		selected = "system"
	}
	choices := append([]pageLocaleSpec{{Code: "system", Endonym: view.locales.text("pref.general.language.system")}}, view.locales.specs...)
	label := view.locales.text("pref.general.language.system")
	var options strings.Builder
	for _, spec := range choices {
		active := spec.Code == selected
		if active {
			label = spec.Endonym
		}
		fmt.Fprintf(&options, `<button type="button" role="option" class="locale-option" data-locale-value="%s" aria-selected="%t">%s</button>`, e(spec.Code), active, e(spec.Endonym))
	}
	language := e(view.locales.text("common.language"))
	return template.HTML(fmt.Sprintf(`<label class="%s"><span>%s</span><span class="locale-picker" data-locale-picker><input type="hidden" name="locale" value="%s" data-locale-input><button type="button" class="locale-toggle" aria-label="%s" aria-haspopup="listbox" aria-expanded="false" data-locale-toggle>%s</button><span class="locale-options" role="listbox" aria-label="%s" hidden>%s</span></span></label>`, e(class), language, e(selected), language, e(label), language, options.String()))
}

func (view *nativePageView) richText(key string, params map[string]template.HTML) template.HTML {
	value := html.EscapeString(view.locales.text(key))
	for name, replacement := range params {
		value = strings.ReplaceAll(value, "{"+name+"}", string(replacement))
	}
	return template.HTML(value)
}

func (view *nativePageView) agentNotice(rows map[string]pageAgentAuth, class string) template.HTML {
	installed, loggedOut := []string{}, []string{}
	for _, agent := range yoagent.ManagedBackends() {
		row := rows[agent]
		if row.Installed {
			installed = append(installed, agent)
			if !row.Available {
				loggedOut = append(loggedOut, agent)
			}
		}
	}
	if len(loggedOut) == 0 {
		return ""
	}
	commands := []string{}
	for _, agent := range loggedOut {
		commands = append(commands, "<code>"+html.EscapeString(pageAgentLoginCommands[agent])+"</code>")
	}
	key, names := "login.agent.login", strings.Join(loggedOut, ", ")
	if len(loggedOut) == len(installed) {
		names = ""
		for index, agent := range loggedOut {
			if index > 0 {
				names += " or "
			}
			names += strings.ToUpper(agent[:1]) + agent[1:]
		}
		key = "login.agent.loginTo"
	}
	lead := template.HTML(html.EscapeString(view.locales.text(key, "names", names)))
	notice := view.richText("login.agent.run", map[string]template.HTML{"lead": lead, "commands": template.HTML(strings.Join(commands, " "))})
	return template.HTML(`<div class="` + html.EscapeString(class) + `">` + string(notice) + `</div>`)
}

func (s *server) pageView(ctx context.Context, preference, language string) (*nativePageView, error) {
	locales, err := s.pageLocales(preference, language)
	if err != nil {
		return nil, err
	}
	version := s.pageVersion(ctx)
	return &nativePageView{Locale: locales.locale, Direction: localeDirection(locales.locale), locales: locales, server: s, version: version, ctx: ctx}, nil
}

func (view *nativePageView) render(name string) ([]byte, error) {
	if err := view.ctx.Err(); err != nil {
		return nil, err
	}
	pages, err := template.New("pages").Funcs(template.FuncMap{"t": view.locales.text, "asset": view.server.staticAssetURL, "brand": view.brand, "localePicker": view.localePicker}).ParseFS(nativePageTemplates, "pages/*.gohtml")
	if err != nil {
		return nil, err
	}
	var output bytes.Buffer
	if err := pages.ExecuteTemplate(&output, name+".gohtml", view); err != nil {
		return nil, err
	}
	if output.Len() > 8*1024*1024 {
		return nil, fmt.Errorf("rendered page exceeds 8 MiB")
	}
	return output.Bytes(), view.ctx.Err()
}

func (s *server) renderPreauthPage(r *http.Request, setup bool, next, message string) ([]byte, error) {
	ctx, release, err := s.beginNativePage(r.Context())
	if err != nil {
		return nil, err
	}
	defer release()
	r = r.WithContext(ctx)
	preference, err := s.requestLocalePreference(r)
	if err != nil {
		return nil, err
	}
	view, err := s.pageView(r.Context(), preference, r.Header.Get("Accept-Language"))
	if err != nil {
		return nil, err
	}
	status, err := s.pageAgentStatus(r.Context(), true)
	if err != nil {
		return nil, err
	}
	t := view.locales.text
	if setup {
		view.AuthPath = filepath.Join(s.config, "auth.yaml")
		login := os.Getenv("USER")
		if current, err := user.Current(); err == nil {
			login = current.Username
		}
		if login == "" {
			return nil, fmt.Errorf("cannot resolve login username")
		}
		view.Login = login
		view.SetupStrings = map[string]string{"waiting": t("setup.waiting"), "waitingServer": t("setup.waitingServer"), "authUpdated": t("setup.authUpdated")}
		view.AgentNoticeHTML = view.agentNotice(status, "setup-login-notice")
		if !s.secureCookies() {
			view.SecurityHTML = template.HTML(`<p id="setupSecurity" class="setup-security">` + string(view.richText("setup.httpsRecommended", map[string]template.HTML{"command": "<code>./boot.sh --force 9998</code>"})) + `</p>`)
		}
		return view.render("setup")
	}
	settings, err := s.nativeSettingsPayload(r.Context(), nil, false)
	if err != nil {
		return nil, err
	}
	appearance := settingsObject(settingsObject(settings["settings"])["appearance"])
	theme, _ := appearance["theme"].(string)
	accent, _ := appearance["active_color"].(string)
	if theme != "dark" && theme != "light" && theme != "system" {
		theme = "dark"
	}
	switch accent {
	case "green", "blue", "orange", "yellow", "purple", "white":
	default:
		accent = "green"
	}
	view.AppearanceClass = "login-theme-" + theme + " login-accent-" + accent
	view.Next = safeLoginNextValue(next)
	view.Error = message
	view.ToggleLabels = map[string]string{"show": t("login.show"), "hide": t("common.hide"), "showAria": t("login.showPassword"), "hideAria": t("login.hidePassword")}
	view.AgentNoticeHTML = view.agentNotice(status, "login-warning")
	if !s.secureCookies() {
		view.SecurityHTML = template.HTML(`<div class="login-warning">` + string(view.richText("login.noHttps", map[string]template.HTML{"command": "<code>./boot.sh --force 9998</code>"})) + `</div>`)
	}
	return view.render("login")
}

func bootstrapSettingsPayload(payload map[string]any) map[string]any {
	result := make(map[string]any)
	for _, key := range []string{"settings", "defaults", "path", "display_path", "mtime_ns", "error", "tabber_mode_explicit"} {
		if value, exists := payload[key]; exists {
			result[key] = value
		}
	}
	overrides := map[string]any{}
	for path, raw := range settingsObject(payload["catalog"]) {
		keys := settingsObject(settingsObject(raw)["locale_keys"])
		entry := map[string]string{}
		for _, name := range []string{"description", "label"} {
			value, _ := keys[name].(string)
			suffix := name
			if name == "description" {
				suffix = "help"
			}
			if value != "" && value != "pref."+path+"."+suffix {
				entry[name] = value
			}
		}
		if len(entry) > 0 {
			overrides[path] = entry
		}
	}
	if len(overrides) > 0 {
		result["localeKeyOverrides"] = overrides
	}
	if len(settingsObject(payload["catalog"])) > 0 || payload["choices"] != nil {
		result["deferred_metadata"] = true
	}
	return result
}

func (s *server) renderNativeHome(r *http.Request, user authUser, topology goTmuxTopology) ([]byte, error) {
	ctx, release, err := s.beginNativePage(r.Context())
	if err != nil {
		return nil, err
	}
	defer release()
	r = r.WithContext(ctx)
	settings, err := s.nativeSettingsPayload(r.Context(), nil, false)
	if err != nil {
		return nil, err
	}
	language := savedLanguagePreference(settings)
	view, err := s.pageView(r.Context(), language, r.Header.Get("Accept-Language"))
	if err != nil {
		return nil, err
	}
	agents, err := s.pageAgentStatus(r.Context(), false)
	if err != nil {
		return nil, err
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return nil, err
	}
	hostname, err := os.Hostname()
	if err != nil {
		return nil, err
	}
	available := []string{}
	for _, agent := range []string{"claude", "codex", "opencode"} {
		if pageCommandAvailable(agent) {
			available = append(available, agent)
		}
	}
	available = append(available, "term")
	launches := map[string]any{}
	for _, agent := range []string{"claude", "codex", "term"} {
		launches[agent] = map[string]string{"normal": goAgentCommand(agent, "", false), "full_access": goAgentCommand(agent, "", true)}
	}
	bootstrap := map[string]any{
		"sessions": topology.sessions, "recentSessions": topology.sessions, "tmuxTopology": map[string]any{"generation": topology.generation, "epoch": s.metadataEpoch()},
		"dev": false, "dangerouslyYolo": s.dangerouslyYolo, "terminalOnly": false, "statsOnly": queryTrue(r.URL.Query().Get("stats_only")), "capabilities": s.capabilities.Snapshot(),
		"sessionMutationsAllowed": user.role == "admin", "availableAgents": available, "terminalCommands": pageTerminalCommands(), "agentLaunchCommands": launches, "agentAuth": agents,
		"accessRole": user.role, "authUsername": user.username, "homePath": home, "repoRoot": s.root, "maxSessionTabs": maxYomuxSessionTabs,
		"filesystemBatchLimits": map[string]int{"maxRequests": finder.MaxBatchRequests, "triggerCountLimit": finder.MaxBatchRequests},
		"serverHostname":        hostname, "cpuTopology": pageCPUTopology(), "serverStartedAt": float64(s.startedAt.UnixNano()) / 1e9, "serverStartedAtMs": s.startedAt.UnixMilli(),
		"linearIssueBaseUrl": "https://linear.app/issue", "backend": goRuntimeLabel(), "version": version, "versionStatus": view.version.Status, "backendLabel": goRuntimeLabel(),
		"postReleaseCommits": view.version.Commits, "clientRevision": s.staticAssetRevision("yomux.js"), "devBundleRevision": s.staticAssetRevision("yomux.js") + "." + s.staticAssetRevision("yomux.css"),
		"versionCommit": view.version.SHA, "versionCommitTime": view.version.TimePT, "versionCommitCount": view.version.Count, "settingsPayload": bootstrapSettingsPayload(settings),
		"activitySummary": map[string]any{"enabled": false, "reason": "async_replacement_required"}, "statsWriterFence": map[string]int{"protocol_version": statsWriterProtocol, "schema_generation": statsSchemaGeneration},
		"locale": view.Locale, "localeRegistry": view.locales.registry(r.Header.Get("Accept-Language")), "strings": view.locales.catalogs, "yoloRulesPayload": s.goYoloPolicyStatus(),
		"codeMirrorAssetUrl": s.staticAssetURL("codemirror.js"), "proseMirrorAssetUrl": s.staticAssetURL("prosemirror.js"),
	}
	if view.version.Error != "" {
		bootstrap["versionMetadataError"] = view.version.Error
	}
	encoded, err := json.Marshal(bootstrap)
	if err != nil {
		return nil, err
	}
	// encoding/json escapes HTML breakout characters, including user session names.
	view.BootstrapJSON = template.JS(encoded)
	return view.render("home")
}
