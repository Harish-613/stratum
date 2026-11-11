// Runs the real Python engine through EngineClient (no VS Code needed):
//   npm run test:engine
import * as assert from 'assert';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { EngineClient } from '../engine';

const repoRoot = path.resolve(__dirname, '..', '..', '..');
const python = process.env.STRATUM_PYTHON || path.join(repoRoot, '.venv', 'bin', 'python');
const engineDir = path.join(repoRoot, 'engine');

suite('EngineClient', function () {
	this.timeout(20000);
	let dir: string;
	let client: EngineClient;

	setup(() => {
		dir = fs.mkdtempSync(path.join(os.tmpdir(), 'stratum-'));
		fs.writeFileSync(path.join(dir, 'main.py'), "import a\nif __name__ == '__main__':\n    pass\n");
		fs.writeFileSync(path.join(dir, 'a.py'), 'import b\n');
		fs.writeFileSync(path.join(dir, 'b.py'), '');
		client = new EngineClient(python, engineDir);
	});

	teardown(async () => {
		await client.dispose();
		fs.rmSync(dir, { recursive: true, force: true });
	});

	test('open returns a report', async () => {
		const r = await client.open(dir);
		assert.strictEqual(r.stats.files, 3);
		assert.deepStrictEqual(r.files['main.py'].imports, ['a.py']);
		assert.deepStrictEqual(r.cycles, []);
	});

	test('update picks up an edit that creates a cycle', async () => {
		await client.open(dir);
		fs.writeFileSync(path.join(dir, 'b.py'), 'import a\n');
		const r = await client.update([path.join(dir, 'b.py')]);
		assert.deepStrictEqual(r.changed, ['b.py']);
		assert.deepStrictEqual(r.cycles, [['a.py', 'b.py']]);
	});

	test('dot in file and folder modes', async () => {
		await client.open(dir);
		assert.match(await client.dot({ mode: 'files' }), /id="file:main.py"/);
		assert.match(await client.dot({ mode: 'folders', depth: 1 }), /id="dir:\(root\)"/);
	});

	test('engine errors reject the request', async () => {
		await assert.rejects(client.request('nope'), /call 'open' before nope/);
	});

	test('a bad python path rejects instead of hanging', async () => {
		const bad = new EngineClient('/definitely/not/python', engineDir);
		await assert.rejects(bad.open(dir), /could not start|exited/);
	});
});
