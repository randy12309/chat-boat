import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Router, CanActivateFn } from '@angular/router';
import { catchError, map, of } from 'rxjs';
import { API_BASE } from './api';

export type ColumnKind = 'text' | 'long' | 'datetime' | 'date' | 'money' | 'badge' | 'number' | 'bool' | 'id';

export interface Column {
  key: string;
  label: string;
  kind: ColumnKind;
}

export type Row = Record<string, string | number | boolean | null>;

export interface TablePage {
  name: string;
  columns: Column[];
  rows: Row[];
  total: number;
  limit: number;
  offset: number;
}

export interface HistoryMessage {
  id: number;
  sequence_number: number;
  role: 'user' | 'assistant';
  content: string;
  sources: string[];
  created_at: string;
  flow_id: string | null;
}

export interface ConversationHistory {
  conversation: { id: string; username: string; title: string; session_id: string; status: string; created_at: string };
  messages: HistoryMessage[];
  truncated: boolean;
}

export interface FlowEvent {
  step: number;
  event_type: string;
  from_actor: string;
  to_actor: string;
  tool_name: string | null;
  status: string | null;
  storage_backend: string | null;
  detail: string;
  elapsed_ms: number;
  duration_ms: number | null;
}

export interface FlowDetail {
  turn: { id: string; username: string | null; session_id: string | null; question: string; final_agent: string | null; duration_ms: number; created_at: string };
  events: FlowEvent[];
}

export interface AgentInfo {
  role: string;
  tools: { name: string; known: boolean }[];
  assigns_to: string[];
}

export interface EmbeddingCollection {
  name: string;
  label: string;
  points: number;
  dimensions: number | null;
  distance: string | null;
}

export interface EmbeddingRow {
  id: string;
  title: string;
  text: string;
  meta: string;
  dimensions: number;
  vector_preview: number[];
}

export interface EmbeddingPage {
  collection: string;
  total: number;
  next_cursor: string | null;
  rows: EmbeddingRow[];
}

export interface EmbeddingDetail {
  id: string;
  collection: string;
  payload: Record<string, unknown>;
  dimensions: number;
  vector: number[];
}

export interface ValkeyEntry {
  key: string;
  question: string;
  answer: string;
  source: string;
  answer_type: string;
  ttl_seconds: number | null;
  dimensions: number;
  vector_preview: number[];
}

export interface ValkeyPage {
  total: number;
  limit: number;
  offset: number;
  rows: ValkeyEntry[];
}

export interface AccountCache {
  username: string;
  data: { kind: string; ttl_seconds: number | null }[];
  answers: { question: string; answer: string; sources: string[]; ttl_seconds: number; semantic: boolean }[];
}

export interface AccountCacheList {
  rows: { username: string; question: string; answer: string; sources: string[]; ttl_seconds: number; semantic: boolean; embedding: number[] | null }[];
  total_users: number;
  offset: number;
  limit: number;
  truncated: boolean;
}

export interface CreateUserRequest {
  username: string;
  email: string;
  display_name: string;
  password: string;
}

export interface CreateUserResponse {
  account_id: number;
  username: string;
}

export interface StagePaymentRequest {
  username: string;
  amount: number;
  epay_method_id?: number;
  due_date?: string; // YYYY-MM-DD
}

export interface StagePaymentResponse {
  payment_id: string;
  amount: number;
  currency: string;
  billing_period: string | null;
  due_date: string | null;
  status: string;
  result: string | null;
  method: string | null;
  method_id: number | null;
  reference: string | null;
  message: string | null;
  paid_at: string | null;
}

export interface PolicyUploadResponse {
  doc_id: string;
  filename: string;
  chunks: number;
  characters: number;
  status: string;
  message: string | null;
}

@Injectable({ providedIn: 'root' })
export class AdminService {
  private readonly http = inject(HttpClient);
  private readonly base = `${API_BASE}/admin`;

  isAdmin() {
    return this.http.get<{ is_admin: boolean }>(`${this.base}/me`).pipe(
      map((r) => r.is_admin),
      catchError((error) => {
        if (error.status === 403) return of(false);
        throw error;
      }),
    );
  }

  table(name: string, opts: { limit: number; offset: number; q?: string; filters?: Record<string, string> }) {
    let params = new HttpParams().set('limit', opts.limit).set('offset', opts.offset);
    if (opts.q) params = params.set('q', opts.q);
    for (const [key, value] of Object.entries(opts.filters ?? {})) params = params.set(key, value);
    return this.http.get<TablePage>(`${this.base}/tables/${name}`, { params });
  }

  history(conversationId: string) {
    return this.http.get<ConversationHistory>(`${this.base}/conversations/${conversationId}/history`);
  }

  flow(turnId: string) {
    return this.http.get<FlowDetail>(`${this.base}/flows/${turnId}`);
  }

  registry() {
    return this.http.get<{ agents: Record<string, AgentInfo>; mcp_tools: string[] }>(`${this.base}/flow-registry`);
  }

  collections() {
    return this.http.get<{ collections: EmbeddingCollection[] }>(`${this.base}/embeddings`).pipe(map((r) => r.collections));
  }

  points(collection: string, limit: number, cursor: string | null) {
    let params = new HttpParams().set('limit', limit);
    if (cursor) params = params.set('cursor', cursor);
    return this.http.get<EmbeddingPage>(`${this.base}/embeddings/${collection}`, { params });
  }

  point(collection: string, id: string) {
    return this.http.get<EmbeddingDetail>(`${this.base}/embeddings/${collection}/${id}`);
  }

  valkeyEntries(opts: { limit: number; offset: number; q?: string }) {
    let params = new HttpParams().set('limit', opts.limit).set('offset', opts.offset);
    if (opts.q) params = params.set('q', opts.q);
    return this.http.get<ValkeyPage>(`${this.base}/valkey-cache`, { params });
  }

  clearFaqCache() {
    return this.http.delete<{ status: string; deleted_keys: number }>(`${this.base}/valkey-cache`);
  }

  uploadFaqCache(file: File) {
    const form = new FormData();
    form.append('file', file);
    return this.http.post<{ filename: string; count: number; status: string }>(`${this.base}/valkey-cache/upload`, form);
  }

  valkeyEntry(key: string) {
    return this.http.get<EmbeddingDetail>(`${this.base}/valkey-cache/entry`, { params: new HttpParams().set('key', key) });
  }

  accountCache(username: string) {
    return this.http.get<AccountCache>(`${this.base}/valkey-account-cache`, { params: new HttpParams().set('username', username) });
  }

  accountCacheList(q: string, limit: number, offset: number) {
    const params = new HttpParams().set('q', q).set('limit', limit).set('offset', offset);
    return this.http.get<AccountCacheList>(`${this.base}/valkey-account-cache/users`, { params });
  }

  createUser(body: CreateUserRequest) {
    return this.http.post<CreateUserResponse>(`${this.base}/tables/user_account`, body);
  }

  stagePayment(body: StagePaymentRequest) {
    return this.http.post<StagePaymentResponse>(`${this.base}/tables/epayment`, body);
  }

  uploadPolicyDocument(file: File) {
    const formData = new FormData();
    formData.append('file', file);
    return this.http.post<PolicyUploadResponse>(`${this.base}/documents/upload-policy`, formData);
  }

  deleteDocument(collection: string, pointId: string) {
    return this.http.delete<{ status: string; point_id: string; collection: string }>(
      `${this.base}/documents/${collection}/${pointId}`
    );
  }

  deleteAllDocuments(collection: string) {
    return this.http.delete<{ status: string; collection: string; deleted: number }>(
      `${this.base}/documents/${collection}`
    );
  }
}

export const adminGuard: CanActivateFn = () => {
  const router = inject(Router);
  return inject(AdminService)
    .isAdmin()
    .pipe(
      map((ok) => ok || router.createUrlTree(['/chat'])),
      catchError(() => of(router.createUrlTree(['/chat']))),
    );
};
