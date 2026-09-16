{{- define "content.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "content.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "content.name" .) | replace "content-content" "content" | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "content.labels" -}}
app.kubernetes.io/name: {{ include "content.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "content.selectorLabelsBase" -}}
app.kubernetes.io/name: {{ include "content.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "content.selectorLabels" -}}
{{ include "content.selectorLabelsBase" . }}
app.kubernetes.io/component: engine
{{- end -}}

{{- define "content.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}

{{/*
The worker workload. It follows the same shape as the UI workloads: one app
name for the release, a `component` label that separates the Deployments.
The API's selector already carries `component: engine`, so the two never
match each other's pods.
*/}}
{{- define "content.workerFullname" -}}
{{- printf "%s-worker" (include "content.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "content.workerSelectorLabels" -}}
{{ include "content.selectorLabelsBase" . }}
app.kubernetes.io/component: worker
{{- end -}}

{{- define "content.workerLabels" -}}
{{ include "content.labels" . }}
app.kubernetes.io/component: worker
{{- end -}}

{{/*
The bundled Ollama, when the chart deploys one. Same shape as the other
workloads: the release's app name, a `component` label of its own.
*/}}
{{- define "content.ollamaFullname" -}}
{{- printf "%s-ollama" (include "content.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "content.ollamaSelectorLabels" -}}
{{ include "content.selectorLabelsBase" . }}
app.kubernetes.io/component: ollama
{{- end -}}

{{- define "content.ollamaLabels" -}}
{{ include "content.labels" . }}
app.kubernetes.io/component: ollama
{{- end -}}

{{/*
The Ollama address the engine should use.

An explicit config.CONTENT_OLLAMA_URL always wins — someone who typed an
address meant it. Otherwise, if the chart deploys an Ollama, its in-cluster
Service is used. Otherwise, empty: no summaries, reported as such.
*/}}
{{- define "content.ollamaUrl" -}}
{{- if .Values.config.CONTENT_OLLAMA_URL -}}
{{ .Values.config.CONTENT_OLLAMA_URL }}
{{- else if .Values.ollama.enabled -}}
http://{{ include "content.ollamaFullname" . }}:11434
{{- end -}}
{{- end -}}

{{/*
The bundled speech service (speech-to-text and text-to-speech), when the chart
deploys one. Same shape as Ollama: a `component` label of its own.
*/}}
{{- define "content.speechFullname" -}}
{{- printf "%s-speech" (include "content.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "content.speechSelectorLabels" -}}
{{ include "content.selectorLabelsBase" . }}
app.kubernetes.io/component: speech
{{- end -}}

{{- define "content.speechLabels" -}}
{{ include "content.labels" . }}
app.kubernetes.io/component: speech
{{- end -}}

{{/*
The speech service address the engine should use. An explicit
config.CONTENT_SPEECH_URL always wins; otherwise the bundled Service when the
chart deploys one; otherwise empty — transcription reported unavailable.
*/}}
{{- define "content.speechUrl" -}}
{{- if .Values.config.CONTENT_SPEECH_URL -}}
{{ .Values.config.CONTENT_SPEECH_URL }}
{{- else if .Values.speech.enabled -}}
http://{{ include "content.speechFullname" . }}:8000
{{- end -}}
{{- end -}}

{{/*
PRELOAD_MODELS as the JSON list speaches reads: the models that are set, in order.
*/}}
{{- define "content.speechPreload" -}}
{{- $models := list -}}
{{- with .Values.speech.sttModel }}{{ $models = append $models . }}{{ end -}}
{{- with .Values.speech.ttsModel }}{{ $models = append $models . }}{{ end -}}
{{- toJson $models -}}
{{- end -}}

{{/*
━━━ Addresses: everything a browser reaches, derived from `domain` ━━━━━━━━━━━

One deployment used to state the same fact in seven places — each ingress
host, publicApiUrl, each surface's publicUrl, CONTENT_PUBLIC_BASE_URL, the
cookie domain, the default sign-in target and the redirect allowlist. Two of
them disagreed in production and the public Sign in button pointed at a LAN
name. So there is one fact, `domain`, and these helpers derive the rest.

Every derived value yields to an explicit one: a named host, a publicUrl, a
config key. The derivation is the default, never a cage.
*/}}

{{- define "content.scheme" -}}
{{- ternary "https" "http" (.Values.https | default false) -}}
{{- end -}}

{{/* The engine's hostname: ingress.host, or <subdomain>.<domain>. */}}
{{- define "content.apiHost" -}}
{{- if .Values.ingress.host -}}
{{ .Values.ingress.host }}
{{- else if .Values.domain -}}
{{ printf "%s.%s" (.Values.ingress.subdomain | default "api") .Values.domain }}
{{- else if .Values.ingress.enabled -}}
{{ fail "Set `domain` (e.g. example.com) or `ingress.host`: the engine needs a name to answer on." }}
{{- end -}}
{{- end -}}

{{/* A surface's hostname. Called with (dict "root" $ "key" $key "ui" $ui). */}}
{{- define "content.uiHost" -}}
{{- $ui := .ui -}}
{{- if (($ui.ingress).host) -}}
{{ $ui.ingress.host }}
{{- else if .root.Values.domain -}}
{{ printf "%s.%s" ($ui.subdomain | default .key) .root.Values.domain }}
{{- else if (($ui.ingress).enabled) -}}
{{ fail (printf "Set `domain`, or `uis.%s.ingress.host`: the %s surface needs a name to answer on." .key .key) }}
{{- end -}}
{{- end -}}

{{/* Where a BROWSER reaches the engine. */}}
{{- define "content.publicApiUrl" -}}
{{- if .Values.publicApiUrl -}}
{{ .Values.publicApiUrl | trimSuffix "/" }}
{{- else -}}
{{- $host := include "content.apiHost" . -}}
{{- if $host -}}{{ printf "%s://%s" (include "content.scheme" .) $host }}{{- end -}}
{{- end -}}
{{- end -}}

{{/* Where a BROWSER reaches a surface. Same arguments as content.uiHost. */}}
{{- define "content.uiPublicUrl" -}}
{{- if .ui.publicUrl -}}
{{ .ui.publicUrl | trimSuffix "/" }}
{{- else -}}
{{- $host := include "content.uiHost" . -}}
{{- if $host -}}{{ printf "%s://%s" (include "content.scheme" .root) $host }}{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The engine's configuration: .Values.config, with every address-shaped key
that was left empty filled in from `domain`. Returned as YAML for fromYaml,
so the ConfigMap keeps one sorted list and a derived key sits where a typed
one would.
*/}}
{{- define "content.effectiveConfig" -}}
{{- $cfg := deepCopy .Values.config -}}
{{- $root := . -}}
{{- $api := include "content.publicApiUrl" . -}}
{{- $surfaces := list -}}
{{- $origins := dict -}}
{{- range $kind, $ui := .Values.uis -}}
{{- if $ui.enabled -}}
{{- $url := include "content.uiPublicUrl" (dict "root" $root "key" $kind "ui" $ui) -}}
{{- if $url -}}
{{- $surfaces = append $surfaces (printf "%s=%s" $kind $url) -}}
{{- $_ := set $origins $kind $url -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if and $api (empty $cfg.CONTENT_PUBLIC_BASE_URL) -}}
{{- $_ := set $cfg "CONTENT_PUBLIC_BASE_URL" $api -}}
{{- end -}}
{{- if and .Values.domain (empty $cfg.CONTENT_SESSION_COOKIE_DOMAIN) -}}
{{- $_ := set $cfg "CONTENT_SESSION_COOKIE_DOMAIN" (printf ".%s" .Values.domain) -}}
{{- end -}}
{{- if empty (toString ($cfg.CONTENT_SESSION_COOKIE_SECURE | default "")) -}}
{{- $_ := set $cfg "CONTENT_SESSION_COOKIE_SECURE" (ternary "true" "false" (.Values.https | default false)) -}}
{{- end -}}
{{- if and $surfaces (empty $cfg.CONTENT_SURFACES) -}}
{{- $_ := set $cfg "CONTENT_SURFACES" (join "," $surfaces) -}}
{{- end -}}
{{- /* Also derived by engines from 0.8.4 on; stated here so an older engine
       returns a visitor to the surface they came from, not to the default. */ -}}
{{- if and $surfaces (empty $cfg.CONTENT_ALLOWED_REDIRECT_ORIGINS) -}}
{{- $ordered := list -}}
{{- range $kind := list "studio" "console" "hometube" -}}
{{- if hasKey $origins $kind -}}{{- $ordered = append $ordered (get $origins $kind) -}}{{- end -}}
{{- end -}}
{{- $_ := set $cfg "CONTENT_ALLOWED_REDIRECT_ORIGINS" (join "," $ordered) -}}
{{- end -}}
{{- if and (hasKey $origins "studio") (empty $cfg.CONTENT_SIGN_IN_DEFAULT_TARGET) -}}
{{- $_ := set $cfg "CONTENT_SIGN_IN_DEFAULT_TARGET" (get $origins "studio") -}}
{{- end -}}
{{- if and (empty $cfg.CONTENT_OLLAMA_URL) .Values.ollama.enabled -}}
{{- $_ := set $cfg "CONTENT_OLLAMA_URL" (include "content.ollamaUrl" .) -}}
{{- if and (empty $cfg.CONTENT_OLLAMA_MODEL) .Values.ollama.models -}}
{{- $_ := set $cfg "CONTENT_OLLAMA_MODEL" (first .Values.ollama.models) -}}
{{- end -}}
{{- end -}}
{{- /* The bundled speech service, same rule as Ollama: an explicit value
       wins, otherwise the Service the chart deploys. */ -}}
{{- if and (empty $cfg.CONTENT_SPEECH_URL) .Values.speech.enabled -}}
{{- $_ := set $cfg "CONTENT_SPEECH_URL" (include "content.speechUrl" .) -}}
{{- if and (empty $cfg.CONTENT_SPEECH_STT_MODEL) .Values.speech.sttModel -}}
{{- $_ := set $cfg "CONTENT_SPEECH_STT_MODEL" .Values.speech.sttModel -}}
{{- end -}}
{{- end -}}
{{- toYaml $cfg -}}
{{- end -}}
