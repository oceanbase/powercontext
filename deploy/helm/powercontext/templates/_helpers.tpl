{{/*
Copyright (c) 2026 OceanBase.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

*/}}
{{- define "powercontext.name" -}}
{{- $name := printf "%s-powercontext" .Release.Name -}}
{{- if gt (len $name) 52 -}}
{{- printf "%s-%s" ($name | trunc 43 | trimSuffix "-") (.Release.Name | sha256sum | trunc 8) -}}
{{- else -}}
{{- $name -}}
{{- end -}}
{{- end -}}
{{- define "powercontext.labels" -}}
app.kubernetes.io/name: powercontext
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end -}}
{{- define "powercontext.image" -}}
{{- $repo := required "image.repository is required" .Values.image.repository -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" $repo .Values.image.digest -}}
{{- else -}}
{{- $tag := required "image.tag or image.digest is required" .Values.image.tag -}}
{{- if eq $tag "latest" -}}{{ fail "image.tag must be pinned; latest is unsupported" }}{{- end -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}
