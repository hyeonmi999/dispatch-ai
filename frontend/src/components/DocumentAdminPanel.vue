<template>
  <div class="documents">
    <form class="document-form" @submit.prevent="save">
      <h3>{{ editing ? '문서 수정' : '문서 추가' }}</h3>
      <label v-if="!editing">
        파일 (PDF·TXT, 최대 20MB)
        <input ref="fileInput" type="file" accept=".pdf,.txt" required :disabled="saving"
          @change="file = $event.target.files?.[0] ?? null" />
      </label>
      <label>제목 <input v-model.trim="form.title" required :disabled="saving" /></label>
      <div class="fields">
        <label>종류
          <select v-model="form.type" :disabled="saving">
            <option v-for="type in types" :key="type">{{ type }}</option>
          </select>
        </label>
        <label>등급
          <select v-model="form.grade" :disabled="saving">
            <option v-for="grade in grades" :key="grade">{{ grade }}</option>
          </select>
        </label>
      </div>
      <label v-if="editing" class="checkbox">
        <input v-model="form.active" type="checkbox" :disabled="saving" /> 사용 중
      </label>
      <div class="actions">
        <n-button v-if="editing" size="small" :disabled="saving" @click="resetForm">취소</n-button>
        <n-button attr-type="submit" type="primary" size="small" :loading="saving"
          :disabled="loading || !!pendingDelete || !form.title || (!editing && !file)">
          {{ editing ? '변경 저장' : '파일 추가' }}
        </n-button>
      </div>
      <p v-if="actionError && !pendingDelete" role="alert" class="error">{{ actionError }}</p>
    </form>

    <p v-if="loading" role="status">문서를 불러오는 중…</p>
    <div v-else-if="loadError" role="alert" class="error">
      <p>{{ loadError }}</p>
      <n-button size="small" @click="loadDocuments">다시 불러오기</n-button>
    </div>
    <p v-else-if="!documents.length" role="status">등록된 문서가 없습니다.</p>
    <div v-else class="table-wrap">
      <table aria-label="법규 문서 목록">
        <thead>
          <tr><th>제목</th><th>종류</th><th>등급</th><th>사용 여부</th><th>등록일</th><th>관리</th></tr>
        </thead>
        <tbody>
          <tr v-for="doc in documents" :key="doc.file">
            <td>{{ doc.title ?? doc.file }}</td>
            <td>{{ doc.type ?? '—' }}</td>
            <td>{{ doc.grade ?? '공개' }}</td>
            <td>{{ doc.active === false ? '사용 안 함' : '사용 중' }}</td>
            <td>{{ doc.created_at ? new Date(doc.created_at).toLocaleDateString('ko-KR') : '—' }}</td>
            <td>
              <div class="actions">
                <n-button size="tiny" :disabled="saving || !!pendingDelete" @click="edit(doc)">수정</n-button>
                <n-button v-if="doc.deletable !== false" size="tiny" type="error" secondary :disabled="saving || !!pendingDelete"
                  @click="confirmDelete(doc)">삭제</n-button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="pendingDelete" class="confirmation" role="group" aria-label="문서 삭제 확인">
      <p>‘{{ pendingDelete.title ?? pendingDelete.file }}’ 문서를 삭제할까요? 파일과 목록 항목이 함께 삭제됩니다.</p>
      <p v-if="actionError" role="alert" class="error">{{ actionError }}</p>
      <div class="actions">
        <n-button size="small" :disabled="saving" @click="cancelDelete">취소</n-button>
        <n-button size="small" type="error" :loading="saving" @click="remove">삭제 확인</n-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { NButton } from 'naive-ui'
import { api, errorMessage } from '../api'

const props = defineProps({ role: { type: String, default: 'citizen' } })

const types = ['법령', '조례', '보도자료', '내부자료']
const grades = ['공개', '내부', '제한']
const emptyForm = () => ({ title: '', type: types[0], grade: grades[0], active: true })

const documents = ref([])
const form = ref(emptyForm())
const file = ref(null)
const fileInput = ref(null)
const editing = ref(null)
const pendingDelete = ref(null)
const loading = ref(true)
const saving = ref(false)
const loadError = ref('')
const actionError = ref('')

async function loadDocuments() {
  loading.value = true
  loadError.value = ''
  try {
    const result = await api.busDocuments(props.role)
    documents.value = Array.isArray(result) ? result : result?.items ?? []
  } catch (error) {
    loadError.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}

function resetForm() {
  editing.value = null
  form.value = emptyForm()
  file.value = null
  if (fileInput.value) fileInput.value.value = ''
  actionError.value = ''
}

function edit(doc) {
  resetForm()
  editing.value = doc.file
  form.value = {
    title: doc.title ?? doc.file,
    type: types.includes(doc.type) ? doc.type : types[0],
    grade: grades.includes(doc.grade) ? doc.grade : grades[0],
    active: doc.active !== false,
  }
}

async function save() {
  if (saving.value) return
  actionError.value = ''
  saving.value = true
  try {
    if (editing.value) {
      await api.updateBusDocument(editing.value, form.value, props.role)
    } else {
      const upload = file.value
      if (!upload || !/\.(pdf|txt)$/i.test(upload.name) || upload.size > 20 * 1024 * 1024) {
        actionError.value = '20MB 이하의 PDF 또는 TXT 파일을 선택해 주세요.'
        return
      }
      const body = new FormData()
      body.append('file', upload)
      for (const field of ['title', 'type', 'grade']) body.append(field, form.value[field])
      await api.createBusDocument(body, props.role)
    }
    resetForm()
    await loadDocuments()
  } catch (error) {
    actionError.value = errorMessage(error)
  } finally {
    saving.value = false
  }
}

function confirmDelete(doc) {
  actionError.value = ''
  pendingDelete.value = doc
}

function cancelDelete() {
  actionError.value = ''
  pendingDelete.value = null
}

async function remove() {
  if (!pendingDelete.value || saving.value) return
  actionError.value = ''
  saving.value = true
  try {
    const name = pendingDelete.value.file
    await api.deleteBusDocument(name, props.role)
    cancelDelete()
    if (editing.value === name) resetForm()
    await loadDocuments()
  } catch (error) {
    actionError.value = errorMessage(error)
  } finally {
    saving.value = false
  }
}

onMounted(loadDocuments)
</script>

<style scoped>
.documents { display: grid; gap: 14px; }
.document-form, .confirmation { display: grid; gap: 8px; padding: 10px; border: 1px solid var(--line); border-radius: 6px; }
h3, p { margin: 0; }
label { display: grid; gap: 4px; min-width: 0; }
input, select { max-width: 100%; min-width: 0; color: inherit; background: var(--panel); border: 1px solid var(--line); padding: 5px; }
.fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.checkbox { display: flex; align-items: center; }
.actions { display: flex; align-items: center; gap: 6px; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 6px; text-align: left; border-bottom: 1px solid var(--line); }
th, td:not(:first-child) { white-space: nowrap; }
.error { color: var(--bad, #b42318); }
</style>
