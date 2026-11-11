import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import { EngineClient, Report } from './engine';
import { GraphPanel } from './graphPanel';

const SOURCE_GLOB = '**/*.{py,js,jsx,mjs,cjs,ts,mts,cts,tsx,go,java,mod}';
const DEBOUNCE_MS = 400;

let engine: EngineClient | undefined;
let report: Report | undefined;
let status: vscode.StatusBarItem;
let output: vscode.OutputChannel;

export function activate(context: vscode.ExtensionContext) {
	output = vscode.window.createOutputChannel('Stratum');
	status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
	status.command = 'stratum.showGraph';
	context.subscriptions.push(output, status);

	context.subscriptions.push(
		vscode.commands.registerCommand('stratum.analyze', () => analyzeWorkspace(context, true)),
		vscode.commands.registerCommand('stratum.showGraph', () =>
			report ? GraphPanel.show(context, getEngine(context), report) : analyzeWorkspace(context, true),
		),
		vscode.commands.registerCommand('stratum.generateDocs', () => generateDocs(context)),
		vscode.commands.registerCommand('stratum.clearApiKey', async () => {
			for (const provider of ['anthropic', 'openai']) {
				await context.secrets.delete(`stratum.apiKey.${provider}`);
			}
			vscode.window.showInformationMessage('Stratum: saved API keys removed.');
		}),
		vscode.workspace.onDidChangeConfiguration(async (e) => {
			if (e.affectsConfiguration('stratum')) {
				await engine?.dispose();
				engine = undefined;
			}
		}),
		{ dispose: () => void engine?.dispose() },
	);
	watchForChanges(context);
}

export function deactivate() {
	return engine?.dispose();
}

function getEngine(context: vscode.ExtensionContext): EngineClient {
	if (!engine) {
		const config = vscode.workspace.getConfiguration('stratum');
		const engineDir = config.get<string>('enginePath') || path.join(context.extensionPath, '..', 'engine');
		const devPython = path.join(context.extensionPath, '..', '.venv', 'bin', 'python');
		const python = config.get<string>('pythonPath') || (fs.existsSync(devPython) ? devPython : 'python3');
		output.appendLine(`Starting engine: ${python} (cwd ${engineDir})`);
		engine = new EngineClient(python, engineDir, (line) => output.appendLine(line));
	}
	return engine;
}

async function analyzeWorkspace(context: vscode.ExtensionContext, showPanel: boolean): Promise<void> {
	const folder = vscode.workspace.workspaceFolders?.[0];
	if (!folder) {
		vscode.window.showWarningMessage('Stratum: open a folder first.');
		return;
	}
	await vscode.window.withProgress(
		{ location: vscode.ProgressLocation.Notification, title: 'Stratum: analyzing workspace…' },
		async () => {
			try {
				report = await getEngine(context).open(folder.uri.fsPath);
				updateStatus(report);
				output.appendLine(`Full scan: ${report.stats.files} files in ${report.timings?.total_s.toFixed(2)} s`);
				if (showPanel) {
					GraphPanel.show(context, getEngine(context), report);
				}
			} catch (err) {
				const msg = (err as Error).message;
				output.appendLine(msg);
				vscode.window.showErrorMessage(`Stratum: ${msg}`, 'Show log').then((pick) => pick && output.show());
			}
		},
	);
}

/** Chunk files at AST boundaries, summarize with an LLM, write .stratum/docs/. */
async function generateDocs(context: vscode.ExtensionContext): Promise<void> {
	const folder = vscode.workspace.workspaceFolders?.[0];
	if (!folder) {
		vscode.window.showWarningMessage('Stratum: open a folder first.');
		return;
	}
	if (!report) {
		await analyzeWorkspace(context, false);
		if (!report) {
			return;
		}
	}
	const config = vscode.workspace.getConfiguration('stratum.docs');
	const provider = config.get<string>('provider') || 'anthropic';
	const secretKey = `stratum.apiKey.${provider}`;
	let apiKey = await context.secrets.get(secretKey);
	if (!apiKey) {
		apiKey = await vscode.window.showInputBox({
			title: `Stratum: ${provider === 'anthropic' ? 'Anthropic' : 'OpenAI'} API key`,
			prompt: 'Stored in VS Code secret storage and only sent to the provider by the local engine.',
			password: true,
			ignoreFocusOut: true,
		});
		if (!apiKey) {
			return;
		}
		await context.secrets.store(secretKey, apiKey);
	}

	const editor = vscode.window.activeTextEditor;
	const scope = await vscode.window.showQuickPick(
		[
			{ label: 'Whole workspace', description: `${report.stats.files} files + ARCHITECTURE.md`, value: 'all' },
			...(editor ? [{ label: 'Current file only', description: vscode.workspace.asRelativePath(editor.document.uri), value: 'file' }] : []),
		],
		{ title: 'Stratum: what should be documented?' },
	);
	if (!scope) {
		return;
	}
	const paths = scope.value === 'file' && editor ? [vscode.workspace.asRelativePath(editor.document.uri, false)] : undefined;

	await vscode.window.withProgress(
		{ location: vscode.ProgressLocation.Notification, title: 'Stratum: generating docs…', cancellable: false },
		async () => {
			try {
				const result = await getEngine(context).request<{ written: string[]; llm_calls: number; cache_hits: number }>('docs', {
					provider,
					model: config.get<string>('model') || undefined,
					api_key: apiKey,
					paths,
				});
				output.appendLine(`Docs: ${result.written.length} files written, ${result.llm_calls} LLM calls, ${result.cache_hits} cached`);
				const first = result.written.find((w) => w.endsWith('ARCHITECTURE.md')) ?? result.written[0];
				if (first) {
					await vscode.window.showTextDocument(vscode.Uri.joinPath(folder.uri, first));
				}
				vscode.window.showInformationMessage(
					`Stratum: wrote ${result.written.length} doc(s) to .stratum/docs (${result.llm_calls} LLM calls, ${result.cache_hits} reused from cache).`,
				);
			} catch (err) {
				const msg = (err as Error).message;
				if (/401|403|authentication|invalid.*key/i.test(msg)) {
					await context.secrets.delete(secretKey);
				}
				vscode.window.showErrorMessage(`Stratum docs failed: ${msg}`);
			}
		},
	);
}

/** Re-analyze changed files incrementally, batching bursts of saves (e.g. a git checkout). */
function watchForChanges(context: vscode.ExtensionContext): void {
	const watcher = vscode.workspace.createFileSystemWatcher(SOURCE_GLOB);
	const pending = new Set<string>();
	let timer: NodeJS.Timeout | undefined;

	const queue = (uri: vscode.Uri) => {
		if (!report || !engine) {
			return; // nothing analyzed yet
		}
		pending.add(uri.fsPath);
		clearTimeout(timer);
		timer = setTimeout(async () => {
			const paths = [...pending];
			pending.clear();
			try {
				const started = Date.now();
				report = await engine!.update(paths);
				if (report.changed && report.changed.length > 0) {
					output.appendLine(`Re-analyzed ${report.changed.length} file(s) in ${Date.now() - started} ms`);
					updateStatus(report);
					GraphPanel.refreshIfOpen(report);
				}
			} catch (err) {
				output.appendLine(`Update failed: ${(err as Error).message}`);
			}
		}, DEBOUNCE_MS);
	};

	watcher.onDidChange(queue);
	watcher.onDidCreate(queue);
	watcher.onDidDelete(queue);
	context.subscriptions.push(watcher, { dispose: () => clearTimeout(timer) });
}

function updateStatus(r: Report): void {
	const s = r.stats;
	const warn = s.cycles > 0 ? `$(warning) ${s.cycles} cycle${s.cycles === 1 ? '' : 's'}` : '$(check) no cycles';
	status.text = `$(type-hierarchy) ${s.files} files · ${warn}`;
	status.tooltip = `Stratum: ${s.loc.toLocaleString()} lines, ${s.unreachable} unreachable files, ${s.duplicate_groups} duplicate groups. Click to open the graph.`;
	status.show();
}
