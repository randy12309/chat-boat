import { NgTemplateOutlet } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import {
  AdminService,
  AgentInfo,
  Column,
  ConversationHistory,
  CreateUserRequest,
  EmbeddingCollection,
  EmbeddingDetail,
  EmbeddingPage,
  FlowDetail,
  Row,
  StagePaymentRequest,
  TablePage,
  ValkeyPage,
} from '../core/admin.service';
import { AuthService } from '../core/auth.service';

interface Tab {
  id: string;
  label: string;
  hint: string;
  table?: string;
}

const PAGE_SIZE = 25;

const TABS: Tab[] = [
  { id: 'admins', label: 'Admins', hint: 'Administrators. Added from ADMIN_USERS at startup.', table: 'admins' },
  { id: 'user_account', label: 'USER_ACCOUNT', hint: 'BMOWNER.USER_ACCOUNT: the member login record. TE_SUID is the user name. Password and OTP are never shown.', table: 'user_account' },
  { id: 'users_profile', label: 'Profiles', hint: 'App-owned display name per member. Names come from MDM in the legacy system, not from USER_ACCOUNT.', table: 'users_profile' },
  { id: 'epayment_method', label: 'EPAYMENT_METHOD', hint: 'BMOWNER.EPAYMENT_METHOD: saved cards and ACH accounts. Tokens and encrypted fields are never shown.', table: 'epayment_method' },
  { id: 'epayment', label: 'EPAYMENT', hint: 'BMOWNER.EPAYMENT staging table: the payments. EPMT_STATUS: P pending (still owed), S synced, F failed. RESULT: A approved, D declined.', table: 'epayment' },
  { id: 'conversations', label: 'Conversations', hint: 'Click a row to read its history.', table: 'conversations' },
  { id: 'messages', label: 'Conversation messages', hint: 'Every stored message. Click a row to open its conversation.', table: 'messages' },
  { id: 'history', label: 'Chat history', hint: 'Pick a conversation to read the full transcript and open the agent flow of each question.' },
  { id: 'embeddings', label: 'Qdrant RAG', hint: 'Policy PDF vectors stored in Qdrant (the RAG fallback). Click a row for the full text and vector.' },
  { id: 'valkey', label: 'Valkey cache', hint: 'FAQ questions cached in Valkey with their embeddings. Only the question is embedded. Click a row for the full vector.' },
  { id: 'activity', label: 'Agent activity', hint: 'One audit table for agent routes, cache and Qdrant retrievals, MCP tool calls, and answers. Click a row for its complete flow.', table: 'agent_activity' },
  { id: 'policy_docs', label: 'Policy Documents', hint: 'Upload policy PDFs. Agentic chunking (Gemini) extracts semantic chunks for vector search.' },
];

const EVENT_LABELS: Record<string, string> = {
  question: 'Question received',
  route: 'Assigned',
  retrieval: 'Retrieval',
  llm: 'LLM call',
  tool_call: 'Tool call',
  tool_result: 'Tool result',
  join: 'Answers combined',
  answer: 'Answer sent',
  sign_in: 'Sign-in required',
};

@Component({
  selector: 'app-admin',
  imports: [RouterLink, NgTemplateOutlet, FormsModule],
  template: `
    <div class="adm">
      <header class="adm-bar">
        <a routerLink="/chat" class="back">&larr; Chat</a>
        <h1>System admin</h1>
        <span class="spacer"></span>
        <span class="who">{{ auth.user() }}</span>
        <button type="button" class="btn-ghost" (click)="auth.logout()">Sign out</button>
      </header>

      <nav class="tabs" aria-label="Admin sections">
        @for (t of tabs; track t.id) {
          <button type="button" [class.on]="tab() === t.id" (click)="select(t.id)">{{ t.label }}</button>
        }
      </nav>

      <main class="panel">
        <p class="hint">{{ tabDef().hint }}</p>
        @if (error()) {
          <div class="error" role="alert">{{ error() }}</div>
        }

        @switch (tab()) {
          @case ('embeddings') {
            <div class="cards">
              @for (c of collections(); track c.name) {
                <button type="button" class="card" [class.on]="collection() === c.name" (click)="openCollection(c.name)">
                  <strong>{{ c.label }}</strong>
                  <span>{{ c.points }} vectors</span>
                  <small>{{ c.dimensions ?? '-' }} dimensions &middot; {{ c.distance ?? 'not created' }}</small>
                </button>
              }
            </div>
            @if (embeddings(); as e) {
              <div class="grid-wrap">
                <table>
                  <thead>
                    <tr><th>Title</th><th>Text</th><th>Metadata</th><th>Vector (first 6 of {{ e.rows[0]?.dimensions ?? 0 }})</th><th style="width: 100px;">Actions</th></tr>
                  </thead>
                  <tbody>
                    @for (r of e.rows; track r.id) {
                      <tr class="click" (click)="openEmbedding(r.id)">
                        <td class="wrap">{{ r.title }}</td>
                        <td class="wrap long">{{ r.text }}</td>
                        <td class="wrap small">{{ r.meta }}</td>
                        <td class="mono small">[{{ r.vector_preview.join(', ') }}, &hellip;]</td>
                        <td>
                          <button type="button" class="btn-danger" (click)="deleteEmbedding(r.id); $event.stopPropagation()"
                            [disabled]="deletingIds().includes(r.id)" title="Delete">
                            {{ deletingIds().includes(r.id) ? '...' : 'Delete' }}
                          </button>
                        </td>
                      </tr>
                    } @empty {
                      <tr><td colspan="5" class="empty">No vectors in this collection.</td></tr>
                    }
                  </tbody>
                </table>
              </div>
              <div class="pager">
                <span>{{ e.total }} vectors</span>
                <button type="button" class="btn-ghost" [disabled]="!cursorStack().length || loading()" (click)="embeddingsPrev()">Previous</button>
                <button type="button" class="btn-ghost" [disabled]="!e.next_cursor || loading()" (click)="embeddingsNext()">Next</button>
                <button type="button" class="btn-danger" (click)="deleteAllEmbeddings()" [disabled]="deletingAll()" title="Delete all vectors in this collection">
                  {{ deletingAll() ? 'Deleting...' : 'Delete All' }}
                </button>
              </div>
            }
          }

          @case ('valkey') {
            <ng-container *ngTemplateOutlet="toolbar" />
            @if (valkey(); as v) {
              <div class="grid-wrap">
                <table>
                  <thead>
                    <tr><th>Question (embedded)</th><th>Answer</th><th>Source</th><th>Type</th><th>TTL (s)</th><th>Vector (first 6 of {{ v.rows[0]?.dimensions ?? 0 }})</th></tr>
                  </thead>
                  <tbody>
                    @for (r of v.rows; track r.key) {
                      <tr class="click" (click)="openValkeyEntry(r.key)">
                        <td class="wrap">{{ r.question }}</td>
                        <td class="wrap long">{{ r.answer }}</td>
                        <td class="small">{{ r.source }}</td>
                        <td class="small">{{ r.answer_type }}</td>
                        <td>{{ r.ttl_seconds ?? 'no expiry' }}</td>
                        <td class="mono small">[{{ r.vector_preview.join(', ') }}, &hellip;]</td>
                      </tr>
                    } @empty {
                      <tr><td colspan="6" class="empty">No cached questions.</td></tr>
                    }
                  </tbody>
                </table>
              </div>
              <div class="pager">
                <span>{{ v.total === 0 ? 0 : v.offset + 1 }}&ndash;{{ min(v.offset + v.limit, v.total) }} of {{ v.total }}</span>
                <button type="button" class="btn-ghost" [disabled]="v.offset === 0 || loading()" (click)="move(-1)">Previous</button>
                <button type="button" class="btn-ghost" [disabled]="v.offset + v.limit >= v.total || loading()" (click)="move(1)">Next</button>
              </div>
            } @else if (loading()) {
              <p class="empty">Loading...</p>
            }
          }

          @case ('policy_docs') {
            <section class="create-form" #uploadForm>
              <h3>Upload Policy Document</h3>
              <form (ngSubmit)="uploadPolicy()" #policyForm="ngForm">
                <div class="form-row">
                  <label>
                    PDF File
                    <input type="file" name="policyFile" accept=".pdf" (change)="onPolicyFileChange($event)" required />
                    @if (policyFile(); as f) {
                      <small class="muted">{{ f.name }} ({{ (f.size / 1024).toFixed(1) }} KB)</small>
                    }
                  </label>
                </div>
                <div class="form-actions">
                  <button type="submit" class="btn" [disabled]="!policyFile() || uploadingPolicy()" [class.loading]="uploadingPolicy()">
                    {{ uploadingPolicy() ? 'Uploading...' : 'Upload & Process' }}
                  </button>
                  <button type="button" class="btn-ghost" (click)="resetPolicyForm()" [disabled]="uploadingPolicy()">Cancel</button>
                </div>
                @if (uploadPolicyError()) {
                  <div class="error">{{ uploadPolicyError() }}</div>
                }
                @if (uploadPolicySuccess()) {
                  <div class="success">{{ uploadPolicySuccess() }}</div>
                }
              </form>
            </section>

            <section class="create-form">
              <h3>Uploaded Documents</h3>
              <div class="toolbar">
                <button type="button" class="btn-ghost" (click)="loadUploadedDocuments()" [disabled]="loadingDocuments()">
                  {{ loadingDocuments() ? 'Loading...' : 'Refresh List' }}
                </button>
              </div>
              @if (loadingDocuments()) {
                <p class="empty">Loading...</p>
              } @else if (uploadedDocuments(); as docs) {
                @if (docs.rows.length > 0) {
                  <div class="grid-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Title</th>
                          <th>Text Preview</th>
                          <th>Metadata</th>
                          <th>Vector</th>
                          <th style="width: 100px;">Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        @for (r of docs.rows; track r.id) {
                          <tr>
                            <td class="wrap">{{ r.title }}</td>
                            <td class="wrap long">{{ r.text }}</td>
                            <td class="wrap small">{{ r.meta }}</td>
                            <td class="mono small">[{{ r.vector_preview.join(', ') }}, &hellip;]</td>
                            <td>
                              <button type="button" class="btn-danger" (click)="deleteDocument(r.id); $event.stopPropagation()"
                                [disabled]="deletingIds().includes(r.id)" title="Delete">
                                {{ deletingIds().includes(r.id) ? '...' : 'Delete' }}
                              </button>
                            </td>
                          </tr>
                        }
                      </tbody>
                    </table>
                  </div>
                  <div class="pager">
                    <span>{{ docs.total }} chunks</span>
                  </div>
                } @else {
                  <p class="empty">No documents uploaded yet. Upload a PDF to get started.</p>
                }
              }
            </section>
          }

          @case ('history') {
            <div class="split">
              <aside class="convs">
                <ng-container *ngTemplateOutlet="toolbar" />
                @for (r of page()?.rows ?? []; track r['id']) {
                  <button type="button" class="conv" [class.on]="history()?.conversation?.id === r['id']" (click)="openHistory(str(r['id']))">
                    <strong>{{ r['title'] }}</strong>
                    <small>{{ r['username'] }} &middot; {{ r['message_count'] }} messages</small>
                  </button>
                } @empty {
                  @if (!loading()) { <p class="empty">No conversations.</p> }
                }
                <ng-container *ngTemplateOutlet="pager" />
              </aside>
              <section class="transcript">
                @if (history(); as h) {
                  <h3>{{ h.conversation.title }}</h3>
                  <p class="small muted">{{ h.conversation.username }} &middot; session {{ h.conversation.session_id }} &middot; {{ h.conversation.status }}</p>
                  @for (m of h.messages; track m.id) {
                    <div class="msg" [class.user]="m.role === 'user'">
                      <div class="msg-head">
                        <span class="badge">{{ m.role }}</span>
                        <small>{{ when(m.created_at) }}</small>
                        @if (m.flow_id) {
                          <button type="button" class="link" (click)="openFlow(m.flow_id)">View agent flow</button>
                        }
                      </div>
                      <div class="msg-body">{{ m.content }}</div>
                      @if (m.sources.length) { <small class="muted">Sources: {{ m.sources.join(', ') }}</small> }
                    </div>
                  }
                  @if (h.truncated) { <p class="muted small">Showing the first 500 messages.</p> }
                } @else {
                  <p class="empty">Select a conversation.</p>
                }
              </section>
            </div>
          }

          @default {
            @if (tab() === 'activity') {
              <details class="registry" (toggle)="loadRegistry()">
                <summary>Agents, their tool functions and who they assign work to</summary>
                @for (a of registryList(); track a.name) {
                  <div class="agent">
                    <strong>{{ a.name }}</strong> <span class="muted">{{ a.info.role }}</span>
                    <div class="chips">
                      @for (t of a.info.tools; track t.name) { <span class="chip tool">{{ t.name }}()</span> }
                      @for (to of a.info.assigns_to; track to) { <span class="chip">&rarr; {{ to }}</span> }
                    </div>
                  </div>
                }
              </details>
            }

            @if (tab() === 'user_account') {
              <section class="create-form" #createUserForm>
                <h3>Create new user</h3>
                <form (ngSubmit)="createUser()" #userForm="ngForm">
                  <div class="form-row">
                    <label>Username <input type="text" name="username" [(ngModel)]="newUserUsername" required maxlength="64" /></label>
                    <label>Email <input type="email" name="email" [(ngModel)]="newUserEmail" required maxlength="254" /></label>
                  </div>
                  <div class="form-row">
                    <label>Display name <input type="text" name="display_name" [(ngModel)]="newUserDisplayName" required maxlength="100" /></label>
                    <label>Password <input type="password" name="password" [(ngModel)]="newUserPassword" required minlength="8" maxlength="72" /></label>
                  </div>
                  <div class="form-actions">
                    <button type="submit" class="btn" [disabled]="userForm.invalid || creatingUser()">Create user</button>
                    <button type="button" class="btn-ghost" (click)="resetUserForm()">Cancel</button>
                  </div>
                  @if (createUserError()) {
                    <div class="error">{{ createUserError() }}</div>
                  }
                  @if (createUserSuccess()) {
                    <div class="success">{{ createUserSuccess() }}</div>
                  }
                </form>
              </section>
            }

            @if (tab() === 'epayment') {
              <section class="create-form" #createPaymentForm>
                <h3>Stage a new payment</h3>
                <form (ngSubmit)="stagePayment()" #paymentForm="ngForm">
                  <div class="form-row">
                    <label>Username (TE_SUID) <input type="text" name="username" [(ngModel)]="newPaymentUsername" required maxlength="64" /></label>
                    <label>Amount <input type="number" name="amount" [(ngModel)]="newPaymentAmount" required step="0.01" min="0.01" /></label>
                  </div>
                  <div class="form-row">
                    <label>Due date (optional) <input type="date" name="due_date" [(ngModel)]="newPaymentDueDate" /></label>
                    <label>Payment method ID (optional) <input type="number" name="epay_method_id" [(ngModel)]="newPaymentMethodId" min="1" /></label>
                  </div>
                  <div class="form-actions">
                    <button type="submit" class="btn" [disabled]="paymentForm.invalid || stagingPayment()">Stage payment</button>
                    <button type="button" class="btn-ghost" (click)="resetPaymentForm()">Cancel</button>
                  </div>
                  @if (stagePaymentError()) {
                    <div class="error">{{ stagePaymentError() }}</div>
                  }
                  @if (stagePaymentSuccess()) {
                    <div class="success">{{ stagePaymentSuccess() }}</div>
                  }
                </form>
              </section>
            }

            <ng-container *ngTemplateOutlet="toolbar" />
            <ng-container *ngTemplateOutlet="grid" />
            <ng-container *ngTemplateOutlet="pager" />
          }
        }
      </main>

      <ng-template #toolbar>
        <form class="toolbar" (submit)="$event.preventDefault(); search()">
          <input type="search" name="q" placeholder="Search..." [value]="q()" (input)="q.set($any($event.target).value)" maxlength="100" />
          <button type="submit" class="btn-ghost">Search</button>
          <button type="button" class="btn-ghost" (click)="reload()">Refresh</button>
        </form>
      </ng-template>

      <ng-template #grid>
        @if (page(); as p) {
          <div class="grid-wrap">
            <table>
              <thead><tr>@for (c of p.columns; track c.key) { <th>{{ c.label }}</th> }</tr></thead>
              <tbody>
                @for (row of p.rows; track $index) {
                  <tr [class.click]="clickable()" (click)="rowClick(row)">
                    @for (c of p.columns; track c.key) {
                      <td [class]="'k-' + c.kind" [class.wrap]="c.kind === 'long'">
                        @if (c.kind === 'badge' && row[c.key]) {
                          <span class="badge" [class]="'badge b-' + badgeClass(row[c.key])">{{ row[c.key] }}</span>
                        } @else {
                          {{ cell(row, c) }}
                        }
                      </td>
                    }
                  </tr>
                } @empty {
                  <tr><td [attr.colspan]="p.columns.length" class="empty">No rows.</td></tr>
                }
              </tbody>
            </table>
          </div>
        } @else if (loading()) {
          <p class="empty">Loading...</p>
        }
      </ng-template>

      <ng-template #pager>
        @if (page(); as p) {
          <div class="pager">
            <span>{{ p.total === 0 ? 0 : p.offset + 1 }}&ndash;{{ min(p.offset + p.limit, p.total) }} of {{ p.total }}</span>
            <button type="button" class="btn-ghost" [disabled]="p.offset === 0 || loading()" (click)="move(-1)">Previous</button>
            <button type="button" class="btn-ghost" [disabled]="p.offset + p.limit >= p.total || loading()" (click)="move(1)">Next</button>
          </div>
        }
      </ng-template>

      @if (flow(); as f) {
        <div class="modal" (click)="flow.set(null)">
          <div class="sheet wide" role="dialog" aria-modal="true" aria-label="Agent flow" (click)="$event.stopPropagation()">
            <button type="button" class="close" (click)="flow.set(null)" aria-label="Close">&times;</button>
            <h3>Agent flow</h3>
            <p class="q">&ldquo;{{ f.turn.question }}&rdquo;</p>
            <p class="small muted">
              {{ f.turn.username ?? 'anonymous' }} &middot; {{ when(f.turn.created_at) }} &middot;
              assigned to <strong>{{ f.turn.final_agent ?? 'supervisor' }}</strong> &middot; {{ f.turn.duration_ms }} ms
            </p>
            <div class="grid-wrap">
              <table>
                <thead><tr><th>#</th><th>Step</th><th>From</th><th></th><th>To</th><th>Tool function</th><th>Storage</th><th>Status</th><th>ms</th><th>Detail</th></tr></thead>
                <tbody>
                  @for (e of f.events; track e.step) {
                    <tr>
                      <td>{{ e.step }}</td>
                      <td>{{ eventLabel(e.event_type) }}</td>
                      <td><span class="badge">{{ e.from_actor }}</span></td>
                      <td>&rarr;</td>
                      <td><span class="badge">{{ e.to_actor || '-' }}</span></td>
                      <td class="mono">{{ e.tool_name ? e.tool_name + '()' : '-' }}</td>
                      <td>@if (e.storage_backend) { <span class="badge">{{ e.storage_backend }}</span> } @else { - }</td>
                      <td>{{ e.status ?? '-' }}</td>
                      <td>{{ e.duration_ms ?? e.elapsed_ms }}</td>
                      <td class="wrap long small">{{ e.detail }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          </div>
        </div>
      }

      @if (embedding(); as d) {
        <div class="modal" (click)="embedding.set(null)">
          <div class="sheet wide" role="dialog" aria-modal="true" aria-label="Embedding" (click)="$event.stopPropagation()">
            <button type="button" class="close" (click)="embedding.set(null)" aria-label="Close">&times;</button>
            <h3>{{ d.collection }}</h3>
            <p class="small mono muted">{{ d.id }}</p>
            <h4>Stored data</h4>
            <pre>{{ pretty(d.payload) }}</pre>
            <h4>Embedding vector ({{ d.dimensions }} numbers)</h4>
            <pre class="vector">{{ vectorText(d) }}</pre>
          </div>
        </div>
      }
    </div>
  `,
  styles: `
    .adm { min-height: 100%; display: flex; flex-direction: column; }
    .adm-bar { display: flex; align-items: center; gap: 12px; padding: 12px 20px; background: #fff; border-bottom: 1px solid #dbe3f5; }
    .adm-bar h1 { margin: 0; font-size: 18px; }
    .back { font-weight: 600; }
    .spacer { flex: 1; }
    .who { font-size: 14px; color: var(--muted); }
    .tabs { display: flex; flex-wrap: wrap; gap: 6px; padding: 12px 20px 0; }
    .tabs button { border: 1px solid #cdd7f0; background: #fff; padding: 8px 14px; border-radius: 10px 10px 0 0; font: inherit; font-size: 14px; cursor: pointer; }
    .tabs button.on { background: var(--primary); color: #fff; border-color: var(--primary); }
    .panel { flex: 1; margin: 0 20px 20px; padding: 16px; background: #fff; border: 1px solid #dbe3f5; border-radius: 0 12px 12px 12px; min-width: 0; }
    .hint { margin: 0 0 12px; color: var(--muted); font-size: 14px; }
    .toolbar { display: flex; gap: 8px; margin-bottom: 10px; }
    .toolbar input { flex: 1; min-width: 0; padding: 8px 10px; border: 1px solid #cbd5e1; border-radius: 8px; font: inherit; font-size: 14px; }
    .grid-wrap { overflow: auto; border: 1px solid #e2e8f0; border-radius: 10px; max-height: 62vh; }
    table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
    th { position: sticky; top: 0; background: #f1f5ff; text-align: left; padding: 8px 10px; font-size: 12px; text-transform: uppercase; letter-spacing: .03em; white-space: nowrap; }
    td { padding: 7px 10px; border-top: 1px solid #eef2f8; vertical-align: top; white-space: nowrap; }
    td.wrap { white-space: normal; min-width: 180px; max-width: 520px; }
    td.k-id, .mono { font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
    td.k-number, td.k-money { text-align: right; }
    tr.click { cursor: pointer; }
    tr.click:hover { background: #f5f8ff; }
    .empty { color: var(--muted); padding: 16px; text-align: center; }
    .small { font-size: 12.5px; }
    .pager { display: flex; align-items: center; gap: 8px; margin-top: 10px; font-size: 13px; }
    .pager span { flex: 1; color: var(--muted); }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; background: #e8eefc; color: #2448c4; font-size: 12px; font-weight: 600; }
    .b-paid, .b-ok, .b-active, .b-hit, .b-assigned { background: #dcfce7; color: #166534; }
    .b-due, .b-overdue, .b-failed, .b-miss, .b-unavailable, .b-timeout { background: #fee2e2; color: #991b1b; }
    .cards { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; }
    .card { display: flex; flex-direction: column; gap: 2px; text-align: left; padding: 10px 14px; background: #fff; border: 1px solid #cdd7f0; border-radius: 12px; font: inherit; cursor: pointer; min-width: 190px; }
    .card.on { border-color: var(--primary); background: #f1f5ff; }
    .split { display: grid; grid-template-columns: minmax(240px, 320px) 1fr; gap: 16px; }
    .convs { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
    .conv { display: flex; flex-direction: column; text-align: left; padding: 9px 12px; background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; font: inherit; cursor: pointer; }
    .conv.on { border-color: var(--primary); background: #f1f5ff; }
    .conv small, .muted { color: var(--muted); }
    .transcript { min-width: 0; max-height: 70vh; overflow: auto; }
    .transcript h3 { margin: 0 0 2px; }
    .msg { margin: 10px 0; padding: 10px 12px; border-radius: 12px; background: #f8fafc; border: 1px solid #e2e8f0; }
    .msg.user { background: #eef2ff; }
    .msg-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
    .msg-body { white-space: pre-wrap; overflow-wrap: anywhere; }
    .link { background: none; border: 0; color: var(--primary); cursor: pointer; font: inherit; font-size: 12.5px; padding: 0; }
    .registry { margin-bottom: 12px; border: 1px solid #e2e8f0; border-radius: 10px; padding: 8px 12px; }
    .registry summary { cursor: pointer; font-weight: 600; font-size: 14px; }
    .agent { margin-top: 10px; }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
    .chip { padding: 2px 9px; border-radius: 8px; background: #f1f5f9; font-size: 12.5px; }
    .chip.tool { background: #ede9fe; color: #5b21b6; font-family: ui-monospace, Consolas, monospace; }
    .modal { position: fixed; inset: 0; background: rgba(15, 23, 42, .45); display: flex; align-items: center; justify-content: center; padding: 20px; z-index: 50; }
    .sheet { position: relative; background: #fff; border-radius: 16px; padding: 20px 22px; width: min(1100px, 100%); max-height: 90vh; overflow: auto; }
    .sheet h3 { margin: 0 0 6px; }
    .sheet h4 { margin: 14px 0 4px; }
    .close { position: absolute; top: 8px; right: 12px; border: 0; background: none; font-size: 26px; cursor: pointer; color: var(--muted); }
    .q { font-family: var(--serif); font-size: 17px; margin: 4px 0; }
    pre { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 10px; overflow: auto; max-height: 260px; font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
    @media (max-width: 800px) { .split { grid-template-columns: 1fr; } }
    .create-form { margin-bottom: 20px; padding: 16px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; }
    .create-form h3 { margin: 0 0 16px; font-size: 16px; }
    .form-row { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 14px; }
    .form-row label { flex: 1; min-width: 220px; display: flex; flex-direction: column; gap: 6px; font-size: 13.5px; font-weight: 500; }
    .form-row input { padding: 9px 12px; border: 1px solid #cbd5e1; border-radius: 8px; font: inherit; font-size: 14px; }
    .form-row input:focus { outline: 2px solid #bfd0ff; border-color: var(--primary); }
    .form-actions { display: flex; gap: 10px; margin-top: 6px; }
    .success { color: #166534; background: #dcfce7; padding: 10px 12px; border-radius: 8px; font-size: 13.5px; margin-top: 10px; }
    .btn-danger { background: #fee2e2; color: #991b1b; border: 1px solid #fecaca; padding: 6px 12px; border-radius: 8px; font: inherit; font-size: 13px; cursor: pointer; }
    .btn-danger:hover:not(:disabled) { background: #fecaca; }
    .btn-danger:disabled { opacity: 0.6; cursor: not-allowed; }
  `,
})
export class AdminComponent {
  private readonly api = inject(AdminService);
  protected readonly auth = inject(AuthService);

  readonly tabs = TABS;
  readonly tab = signal('user_account');
  readonly tabDef = computed(() => TABS.find((t) => t.id === this.tab()) ?? TABS[0]);
  readonly page = signal<TablePage | null>(null);
  readonly q = signal('');
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  readonly history = signal<ConversationHistory | null>(null);
  readonly flow = signal<FlowDetail | null>(null);
  readonly registry = signal<Record<string, AgentInfo> | null>(null);
  readonly registryList = computed(() => Object.entries(this.registry() ?? {}).map(([name, info]) => ({ name, info })));

  readonly collections = signal<EmbeddingCollection[]>([]);
  readonly collection = signal<string | null>(null);
  readonly embeddings = signal<EmbeddingPage | null>(null);
  readonly embedding = signal<EmbeddingDetail | null>(null);
  readonly valkey = signal<ValkeyPage | null>(null);
  readonly cursorStack = signal<(string | null)[]>([]);
  private cursor: string | null = null;

  readonly clickable = computed(() => ['conversations', 'messages', 'activity'].includes(this.tab()));
  private requestId = 0;

  // Create user form state
  readonly newUserUsername = signal('');
  readonly newUserEmail = signal('');
  readonly newUserDisplayName = signal('');
  readonly newUserPassword = signal('');
  readonly creatingUser = signal(false);
  readonly createUserError = signal<string | null>(null);
  readonly createUserSuccess = signal<string | null>(null);

  // Stage payment form state
  readonly newPaymentUsername = signal('');
  readonly newPaymentAmount = signal<number | null>(null);
  readonly newPaymentMethodId = signal<number | null>(null);
  readonly newPaymentDueDate = signal(''); // YYYY-MM-DD
  readonly stagingPayment = signal(false);
  readonly stagePaymentError = signal<string | null>(null);
  readonly stagePaymentSuccess = signal<string | null>(null);

  // Policy document upload form state
  readonly policyFile = signal<File | null>(null);
  readonly uploadingPolicy = signal(false);
  readonly uploadPolicyError = signal<string | null>(null);
  readonly uploadPolicySuccess = signal<string | null>(null);
  readonly uploadedDocuments = signal<EmbeddingPage | null>(null);
  readonly loadingDocuments = signal(false);
  readonly deletingIds = signal<string[]>([]);
  readonly deletingAll = signal(false);

  constructor() {
    void this.load(0);
  }

  select(id: string): void {
    if (this.tab() === id) return;
    this.tab.set(id);
    this.q.set('');
    this.page.set(null);
    this.valkey.set(null);
    this.error.set(null);
    void this.load(0);
  }

  search(): void {
    void this.load(0);
  }

  reload(): void {
    void this.load((this.tab() === 'valkey' ? this.valkey()?.offset : this.page()?.offset) ?? 0);
  }

  move(direction: 1 | -1): void {
    const p = this.tab() === 'valkey' ? this.valkey() : this.page();
    if (p) void this.load(Math.max(0, p.offset + direction * p.limit));
  }

  private async load(offset: number): Promise<void> {
    const id = ++this.requestId;
    this.error.set(null);
    this.loading.set(true);
    try {
      const tab = this.tabDef();
      if (tab.id === 'embeddings') {
        const list = await firstValueFrom(this.api.collections());
        if (id !== this.requestId) return;
        this.collections.set(list);
        const current = this.collection() ?? list.find((c) => c.points > 0)?.name ?? list[0]?.name ?? null;
        if (current) await this.openCollection(current);
        return;
      }
      if (tab.id === 'valkey') {
        const entries = await firstValueFrom(this.api.valkeyEntries({ limit: PAGE_SIZE, offset, q: this.q().trim() }));
        if (id === this.requestId) this.valkey.set(entries);
        return;
      }
      const name = tab.table ?? 'conversations'; // the history tab lists conversations
      const page = await firstValueFrom(this.api.table(name, { limit: PAGE_SIZE, offset, q: this.q().trim() }));
      if (id === this.requestId) this.page.set(page);
    } catch (err) {
      if (id === this.requestId) this.error.set(this.message(err));
    } finally {
      if (id === this.requestId) this.loading.set(false);
    }
  }

  rowClick(row: Row): void {
    const tab = this.tab();
    if (tab === 'conversations') void this.openHistory(this.str(row['id']));
    else if (tab === 'messages') void this.openHistory(this.str(row['conversation_id']));
    else if (tab === 'activity') void this.openFlow(this.str(row['turn_id']));
  }

  async openHistory(conversationId: string): Promise<void> {
    if (this.tab() !== 'history') {
      this.tab.set('history');
      this.q.set('');
      this.page.set(null);
      void this.load(0);
    }
    try {
      this.history.set(await firstValueFrom(this.api.history(conversationId)));
    } catch (err) {
      this.error.set(this.message(err));
    }
  }

  async openFlow(turnId: string): Promise<void> {
    try {
      this.flow.set(await firstValueFrom(this.api.flow(turnId)));
    } catch (err) {
      this.error.set(this.message(err));
    }
  }

  async loadRegistry(): Promise<void> {
    if (this.registry()) return;
    try {
      this.registry.set((await firstValueFrom(this.api.registry())).agents);
    } catch (err) {
      this.error.set(this.message(err));
    }
  }

  async createUser(): Promise<void> {
    this.createUserError.set(null);
    this.createUserSuccess.set(null);
    this.creatingUser.set(true);
    try {
      const body: CreateUserRequest = {
        username: this.newUserUsername(),
        email: this.newUserEmail(),
        display_name: this.newUserDisplayName(),
        password: this.newUserPassword(),
      };
      const res = await firstValueFrom(this.api.createUser(body));
      this.createUserSuccess.set(`User "${res.username}" created (account_id: ${res.account_id})`);
      this.resetUserForm();
      void this.load(this.page()?.offset ?? 0);
    } catch (err) {
      this.createUserError.set(this.message(err));
    } finally {
      this.creatingUser.set(false);
    }
  }

  resetUserForm(): void {
    this.newUserUsername.set('');
    this.newUserEmail.set('');
    this.newUserDisplayName.set('');
    this.newUserPassword.set('');
    this.createUserError.set(null);
    this.createUserSuccess.set(null);
  }

  async stagePayment(): Promise<void> {
    this.stagePaymentError.set(null);
    this.stagePaymentSuccess.set(null);
    this.stagingPayment.set(true);
    try {
      const body: StagePaymentRequest = {
        username: this.newPaymentUsername(),
        amount: this.newPaymentAmount() ?? 0,
      };
      const methodId = this.newPaymentMethodId();
      if (methodId !== null) body.epay_method_id = methodId;
      const dueDate = this.newPaymentDueDate();
      if (dueDate) body.due_date = dueDate;
      const res = await firstValueFrom(this.api.stagePayment(body));
      this.stagePaymentSuccess.set(`Payment staged: ${res.payment_id} (${res.amount} ${res.currency})`);
      this.resetPaymentForm();
      void this.load(this.page()?.offset ?? 0);
    } catch (err) {
      this.stagePaymentError.set(this.message(err));
    } finally {
      this.stagingPayment.set(false);
    }
  }

  resetPaymentForm(): void {
    this.newPaymentUsername.set('');
    this.newPaymentAmount.set(null);
    this.newPaymentMethodId.set(null);
    this.newPaymentDueDate.set('');
    this.stagePaymentError.set(null);
    this.stagePaymentSuccess.set(null);
  }

  onPolicyFileChange(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (input.files && input.files.length > 0) {
      const file = input.files[0];
      if (file.type !== 'application/pdf') {
        this.uploadPolicyError.set('Please select a PDF file.');
        this.policyFile.set(null);
        input.value = '';
        return;
      }
      this.policyFile.set(file);
      this.uploadPolicyError.set(null);
      this.uploadPolicySuccess.set(null);
    }
  }

  async uploadPolicy(): Promise<void> {
    const file = this.policyFile();
    if (!file) return;

    this.uploadPolicyError.set(null);
    this.uploadPolicySuccess.set(null);
    this.uploadingPolicy.set(true);

    try {
      const res = await firstValueFrom(this.api.uploadPolicyDocument(file));
      this.uploadPolicySuccess.set(
        `Document "${res.filename}" processed: ${res.chunks} chunks created (${res.characters} characters). ${res.message ?? ''}`
      );
      this.policyFile.set(null);
      // Reset file input
      const fileInput = document.querySelector('input[name="policyFile"]') as HTMLInputElement;
      if (fileInput) fileInput.value = '';
      // Reload the document list
      await this.loadUploadedDocuments();
    } catch (err) {
      this.uploadPolicyError.set(this.message(err));
    } finally {
      this.uploadingPolicy.set(false);
    }
  }

  resetPolicyForm(): void {
    this.policyFile.set(null);
    this.uploadPolicyError.set(null);
    this.uploadPolicySuccess.set(null);
    const fileInput = document.querySelector('input[name="policyFile"]') as HTMLInputElement;
    if (fileInput) fileInput.value = '';
  }

  async loadUploadedDocuments(): Promise<void> {
    this.loadingDocuments.set(true);
    this.uploadedDocuments.set(null);
    try {
      // Use the uploaded_documents collection
      const docs = await firstValueFrom(this.api.points('uploaded_documents', 50, null));
      this.uploadedDocuments.set(docs);
    } catch (err) {
      this.error.set(this.message(err));
    } finally {
      this.loadingDocuments.set(false);
    }
  }

  async deleteDocument(pointId: string): Promise<void> {
    if (this.deletingIds().includes(pointId)) return;
    this.deletingIds.update((ids) => [...ids, pointId]);
    try {
      await firstValueFrom(this.api.deleteDocument('uploaded_documents', pointId));
      // Remove from local list
      this.uploadedDocuments.update((docs) => {
        if (!docs) return docs;
        return { ...docs, rows: docs.rows.filter((r) => r.id !== pointId), total: docs.total - 1 };
      });
    } catch (err) {
      this.error.set(this.message(err));
    } finally {
      this.deletingIds.update((ids) => ids.filter((id) => id !== pointId));
    }
  }

  async openCollection(name: string): Promise<void> {
    this.collection.set(name);
    this.cursorStack.set([]);
    await this.loadPoints(null);
  }

  async embeddingsNext(): Promise<void> {
    const next = this.embeddings()?.next_cursor;
    if (!next) return;
    this.cursorStack.update((s) => [...s, this.cursor]);
    await this.loadPoints(next);
  }

  async embeddingsPrev(): Promise<void> {
    const stack = this.cursorStack();
    if (!stack.length) return;
    this.cursorStack.set(stack.slice(0, -1));
    await this.loadPoints(stack[stack.length - 1]);
  }

  private async loadPoints(cursor: string | null): Promise<void> {
    const name = this.collection();
    if (!name) return;
    this.loading.set(true);
    this.error.set(null);
    try {
      this.embeddings.set(await firstValueFrom(this.api.points(name, 20, cursor)));
      this.cursor = cursor;
    } catch (err) {
      this.error.set(this.message(err));
    } finally {
      this.loading.set(false);
    }
  }

  async deleteEmbedding(pointId: string): Promise<void> {
    const collection = this.collection();
    if (!collection || this.deletingIds().includes(pointId)) return;
    this.deletingIds.update((ids) => [...ids, pointId]);
    try {
      await firstValueFrom(this.api.deleteDocument(collection, pointId));
      this.embeddings.update((e) => {
        if (!e) return e;
        return { ...e, rows: e.rows.filter((r) => r.id !== pointId), total: e.total - 1 };
      });
    } catch (err) {
      this.error.set(this.message(err));
    } finally {
      this.deletingIds.update((ids) => ids.filter((id) => id !== pointId));
    }
  }

  async deleteAllEmbeddings(): Promise<void> {
    const collection = this.collection();
    if (!collection || this.deletingAll()) return;
    this.deletingAll.set(true);
    try {
      const res = await firstValueFrom(this.api.deleteAllDocuments(collection));
      this.embeddings.update((e) => {
        if (!e) return e;
        return { ...e, rows: [], total: 0 };
      });
    } catch (err) {
      this.error.set(this.message(err));
    } finally {
      this.deletingAll.set(false);
    }
  }

  async openValkeyEntry(key: string): Promise<void> {
    try {
      this.embedding.set(await firstValueFrom(this.api.valkeyEntry(key)));
    } catch (err) {
      this.error.set(this.message(err));
    }
  }

  async openEmbedding(id: string): Promise<void> {
    const name = this.collection();
    if (!name) return;
    try {
      this.embedding.set(await firstValueFrom(this.api.point(name, id)));
    } catch (err) {
      this.error.set(this.message(err));
    }
  }

  cell(row: Row, col: Column): string {
    const v = row[col.key];
    if (v === null || v === undefined || v === '') return '-';
    switch (col.kind) {
      case 'datetime':
        return this.when(String(v));
      case 'money':
        return Number(v).toFixed(2);
      case 'bool':
        return v ? 'Yes' : 'No';
      default:
        return String(v);
    }
  }

  when(value: string): string {
    const d = new Date(value);
    return isNaN(d.getTime()) ? value : d.toLocaleString();
  }

  badgeClass(value: Row[string]): string {
    return String(value).toLowerCase().replace(/[^a-z]/g, '');
  }

  eventLabel(type: string): string {
    return EVENT_LABELS[type] ?? type;
  }

  str(value: Row[string]): string {
    return String(value ?? '');
  }

  min(a: number, b: number): number {
    return Math.min(a, b);
  }

  pretty(value: unknown): string {
    return JSON.stringify(value, null, 2);
  }

  vectorText(d: EmbeddingDetail): string {
    return d.vector.map((x) => x.toFixed(5)).join(', ');
  }

  private message(err: unknown): string {
    if (err instanceof HttpErrorResponse) {
      if (err.status === 403) return 'Administrator access is required.';
      const detail = err.error?.detail;
      return typeof detail === 'string' ? detail : `The server returned an error (${err.status}).`;
    }
    return 'Something went wrong. Try again.';
  }
}
