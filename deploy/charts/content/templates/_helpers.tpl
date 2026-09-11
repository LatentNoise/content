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
