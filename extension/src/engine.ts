import { ChildProcess, spawn } from 'child_process';
import * as readline from 'readline';

/** Subset of the engine's analysis report that the extension reads. */
export interface Report {
	root: string;
	stats: {
		files: number;
		loc: number;
		languages: Record<string, number>;
		edges: number;
		cycles: number;
		unreachable: number;
		duplicate_groups: number;
		complex_functions: number;
	};
	files: Record<string, { language: string; loc: number; fan_in: number; fan_out: number; imports: string[] }>;
	cycles: string[][];
	entry_points: string[];
	unreachable: string[];
	duplicates: { lines: number; instances: { file: string; name: string; line: number; end_line: number }[] }[];
	complex_functions: { file: string; name: string; line: number; complexity: number }[];
	changed?: string[];
	timings?: { total_s: number };
}

type Pending = { resolve: (value: any) => void; reject: (err: Error) => void };

/**
 * Talks to `python -m stratum_engine.cli serve` over newline-delimited JSON.
 * One long-lived process, so parse results stay cached between requests.
 */
export class EngineClient {
	private proc: ChildProcess | undefined;
	private nextId = 1;
	private pending = new Map<number, Pending>();

	constructor(
		private readonly python: string,
		private readonly cwd: string,
		private readonly log: (line: string) => void = () => {},
	) {}

	start(): void {
		if (this.proc) {
			return;
		}
		const proc = spawn(this.python, ['-m', 'stratum_engine.cli', 'serve'], { cwd: this.cwd });
		this.proc = proc;
		readline.createInterface({ input: proc.stdout! }).on('line', (line) => this.onLine(line));
		readline.createInterface({ input: proc.stderr! }).on('line', (line) => this.log(line));
		const fail = (why: string) => {
			this.proc = undefined;
			for (const p of this.pending.values()) {
				p.reject(new Error(why));
			}
			this.pending.clear();
		};
		proc.on('error', (err) => fail(`could not start the Stratum engine (${this.python}): ${err.message}`));
		proc.on('exit', (code) => fail(`Stratum engine exited (code ${code})`));
	}

	request<T = any>(method: string, params: object = {}): Promise<T> {
		this.start();
		const id = this.nextId++;
		return new Promise<T>((resolve, reject) => {
			this.pending.set(id, { resolve, reject });
			this.proc!.stdin!.write(JSON.stringify({ id, method, params }) + '\n');
		});
	}

	open(root: string): Promise<Report> {
		return this.request<Report>('open', { root });
	}

	update(paths: string[]): Promise<Report> {
		return this.request<Report>('update', { paths });
	}

	async dot(options: { mode: 'files' | 'folders'; focus?: string; depth?: number }): Promise<string> {
		return (await this.request<{ dot: string }>('dot', options)).dot;
	}

	async dispose(): Promise<void> {
		if (!this.proc) {
			return;
		}
		const done = new Promise<void>((resolve) => this.proc?.once('exit', () => resolve()));
		this.request('shutdown').catch(() => {});
		const timeout = new Promise<void>((resolve) => setTimeout(resolve, 1000));
		await Promise.race([done, timeout]);
		this.proc?.kill();
		this.proc = undefined;
	}

	private onLine(line: string): void {
		let msg: { id: number; result?: unknown; error?: string };
		try {
			msg = JSON.parse(line);
		} catch {
			this.log(`unparseable engine output: ${line.slice(0, 200)}`);
			return;
		}
		const p = this.pending.get(msg.id);
		if (!p) {
			return;
		}
		this.pending.delete(msg.id);
		if (msg.error) {
			p.reject(new Error(msg.error));
		} else {
			p.resolve(msg.result);
		}
	}
}
