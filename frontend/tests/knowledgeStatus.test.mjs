import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import { SourceTextModule, SyntheticModule } from 'node:vm'

async function loadApi(get, post = async () => ({ data: 'answer' })) {
  const module = new SourceTextModule(await readFile(new URL('../src/api.js', import.meta.url), 'utf8'))
  await module.link((specifier) => {
    if (specifier === 'axios') return new SyntheticModule(['default'], function () {
      this.setExport('default', { create: () => ({ get, post }) })
    })
    if (specifier === './theme') return new SyntheticModule(['palette'], function () {
      this.setExport('palette', { value: {} })
    })
    throw new Error(`Unexpected dependency: ${specifier}`)
  })
  await module.evaluate()
  return module.namespace.api
}

test('successful status retains the rebuilding notice', async () => {
  const api = await loadApi(async () => ({ data: { rebuild_required: true } }))
  assert.equal((await api.knowledgeStatus('bus', 'bge')).rebuild_required, true)
})

for (const reason of ['HTTP 500', 'timeout']) {
  test(`status ${reason} does not prevent ask or chat`, async () => {
    const sent = []
    const api = await loadApi(async () => { throw new Error(reason) }, async (url) => {
      sent.push(url)
      return { data: 'answer' }
    })
    assert.equal(await api.knowledgeStatus('bus', 'bge'), null)
    assert.equal(await api.ask('bus', { question: 'procedure' }), 'answer')
    assert.equal(await api.chat('bus', { message: 'procedure' }), 'answer')
    assert.deepEqual(sent, ['/bus/ask', '/bus/chat'])
  })
}

test('actual question errors remain visible', async () => {
  const api = await loadApi(async () => ({ data: {} }), async () => { throw new Error('ask failed') })
  await assert.rejects(api.ask('bus', {}), /ask failed/)
})
