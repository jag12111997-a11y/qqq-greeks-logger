const fs = require('fs');
const vm = require('vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(require('path').join(__dirname, '../yama-dashboard/dist/gshinrje.html'), 'utf8');
const source = html.slice(html.indexOf('var notebookDrafts ='), html.indexOf('// WORKSPACE SHELL'));
const stored = new Map([['gshinrje-quicknotes', 'Previously saved paragraph'], ['gshinrje-notes-2026-09-15', 'Earlier observations']]);
const events = {};
const context = vm.createContext({
  esc: value => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
  localStorage: {getItem:key=>stored.get(key)||null, setItem:(key,value)=>stored.set(key,value)},
  document: {addEventListener:(type,fn)=>events[type]=fn},
});
vm.runInContext(source, context);
const key = 'gshinrje-notebook-v1-first';
const draft = context.readNotebookDraft(key, '2026-09-15');
assert.equal(draft.fields.notes, 'Previously saved paragraph');
assert.equal(draft.fields.observations, 'Earlier observations');
let status = {};
const root = {dataset:{notebookKey:key},querySelector:()=>status};
const field = {dataset:{notebookField:'journal'}, value:'Paragraph\n<text> & "quotes"',closest:()=>root};
events.input({target:{closest:()=>field}});
assert.equal(JSON.parse(stored.get(key)).fields.journal, field.value);
assert.equal(status.textContent, 'Saved in this browser');
vm.runInContext('notebookDrafts = Object.create(null)', context);
assert.equal(context.readNotebookDraft(key, '2026-09-15').fields.journal, field.value);
assert.equal(context.readNotebookDraft('second', '2026-09-15').fields.journal, undefined);
const rendered = context.renderNotebook({entry_date:'2026-09-15'}, {id:'first'});
assert.ok(rendered.includes('Paragraph\n&lt;text&gt; &amp; "quotes"'));
assert.ok(!rendered.includes('<script>'));
context.localStorage.setItem = ()=>{throw Error('Storage unavailable');};
context.saveNotebookDraft(root);
assert.match(status.textContent, /Could not save/);
assert.equal(context.notebookDrafts[key].fields.journal, field.value);
console.log('PASS: legacy-note recovery, autosave, reload restoration, independent drafts, escaped text, storage-failure feedback');
