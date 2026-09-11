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
