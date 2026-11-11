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
