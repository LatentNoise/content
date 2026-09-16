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
