{{/* Labels for one component; selectors use only app.kubernetes.io/name, which never changes (selectors are immutable). */}}
{{- define "six.labels" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/part-of: six-sevens
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "six.selector" -}}
app.kubernetes.io/name: {{ .name }}
{{- end }}

{{/* A backend role's image: its own, else the default. */}}
{{- define "six.backendImage" -}}
{{- $image := index .root.Values.images.backend .name | default .root.Values.images.backendDefault -}}
{{- required (printf "images.backend.%s (or images.backendDefault) is required" .name) $image -}}
{{- end }}

{{/* The pod's Kubernetes account, acting as its Google account (Workload Identity). */}}
{{- define "six.serviceAccount" -}}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ .name }}
  namespace: {{ .namespace }}
  labels:
    {{- include "six.labels" . | nindent 4 }}
  {{- if .root.Values.workloadIdentity.enabled }}
  annotations:
    iam.gke.io/gcp-service-account: run-{{ .name }}@{{ required "projectId is required" .root.Values.projectId }}.iam.gserviceaccount.com
  {{- end }}
automountServiceAccountToken: false
{{- end }}

{{/* Secret Manager secrets as files, through the GKE add-on (csi mode only). .secrets: [{env, id}]. */}}
{{- define "six.secretProviderClass" -}}
{{- if and .secrets (eq .root.Values.secrets.mode "csi") }}
---
apiVersion: secrets-store.csi.x-k8s.io/v1
kind: SecretProviderClass
metadata:
  name: {{ .name }}-secrets
  namespace: {{ .namespace }}
spec:
  provider: gke
  parameters:
    secrets: |
      {{- range .secrets }}
      - resourceName: "projects/{{ $.root.Values.projectId }}/secrets/{{ .id }}/versions/latest"
        path: "{{ .env }}"
      {{- end }}
{{- end }}
{{- end }}

{{- define "six.secretVolume" -}}
- name: secrets
  {{- if eq .root.Values.secrets.mode "csi" }}
  csi:
    driver: secrets-store-gke.csi.k8s.io
    readOnly: true
    volumeAttributes:
      secretProviderClass: {{ .name }}-secrets
  {{- else }}
  secret:
    secretName: app-secrets
  {{- end }}
{{- end }}

{{/* Exports each mounted secret file as the variable it's named after (a trailing newline is dropped), then runs the command. */}}
{{- define "six.withSecrets" -}}
for f in {{ .Values.secrets.mountPath }}/*; do [ -f "$f" ] && export "$(basename "$f")=$(cat "$f")"; done; exec
{{- end }}

{{/* CPU and memory, requests equal to limits (Autopilot bills requests). */}}
{{- define "six.resources" -}}
{{- $cpu := .root.Values.resourcesOverride.cpu | default .cpu -}}
{{- $memory := .root.Values.resourcesOverride.memory | default .memory -}}
requests:
  cpu: {{ $cpu | quote }}
  memory: {{ $memory | quote }}
  ephemeral-storage: {{ .ephemeral | default "1Gi" | quote }}
limits:
  cpu: {{ $cpu | quote }}
  memory: {{ $memory | quote }}
  ephemeral-storage: {{ .ephemeral | default "1Gi" | quote }}
{{- end }}

{{- define "six.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: {{ .readOnly }}
capabilities:
  drop: ["ALL"]
{{- end }}

{{/* Spread a component's replicas across zones (the cluster is regional). */}}
{{- define "six.spread" -}}
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: topology.kubernetes.io/zone
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        {{- include "six.selector" . | nindent 8 }}
{{- end }}
