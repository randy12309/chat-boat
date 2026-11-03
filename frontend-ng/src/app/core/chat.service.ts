import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { map } from 'rxjs';
import { API_BASE } from './api';
import { AuthService } from './auth.service';

export interface PremiumAlert {
  amount: number;
  currency: string;
  due_date: string | null;
  status: string;
}

export interface HomeData {
  welcome: { display_name: string; message: string };
  premium_alert: PremiumAlert | null;
  inbox: { unread_count: number; latest_subject: string | null };
  suggested_questions: string[];
}

export interface ChatSession {
  session_id: string;
  title: string;
  updated_at: number;
}

export interface HistoryMessage {
  role: 'user' | 'assistant';
  content: string;
  time?: number;
}

@Injectable({ providedIn: 'root' })
export class ChatService {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(AuthService);

  home() {
    return this.http.get<HomeData>(`${API_BASE}/home`);
  }

  send(message: string, context: unknown, sessionId: string) {
    return this.http
      .post<{ reply: string }>(`${API_BASE}/chat`, { message, context: context ?? null, session_id: sessionId })
      .pipe(map((res) => res.reply));
  }

  /**
   * Streams the reply over server-sent events. `onText` receives the full reply-so-far each time
   * more text arrives (it can shrink if the server discards a partial answer). Resolves with the final reply.
   */
  async stream(message: string, sessionId: string, onText: (text: string) => void): Promise<string> {
    const res = await fetch(`${API_BASE}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${this.auth.token()}` },
      body: JSON.stringify({ message, session_id: sessionId }),
    });
    if (res.status === 401) {
      this.auth.logout();
      throw new HttpErrorResponse({ status: 401 });
    }
    if (!res.ok || !res.body) throw new Error(`Stream failed (${res.status})`);

    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = '';
    let text = '';
    let final: string | null = null;
    const handle = (line: string) => {
      if (!line.startsWith('data: ')) return;
      const event = JSON.parse(line.slice(6));
      if (event.error) throw new Error(event.error);
      if (event.reset) text = '';
      if (event.delta) text += event.delta;
      if (event.reset || event.delta) onText(text);
      if (event.done) final = event.reply;
    };
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      lines.forEach(handle);
    }
    if (buffer) handle(buffer);
    if (final === null) throw new Error('Stream ended early');
    return final;
  }

  sessions() {
    return this.http.get<{ sessions: ChatSession[] }>(`${API_BASE}/chat/sessions`).pipe(map((d) => d.sessions));
  }

  history(sessionId: string) {
    return this.http
      .get<{ history: HistoryMessage[] }>(`${API_BASE}/chat/sessions/${sessionId}`)
      .pipe(map((d) => d.history));
  }

  rename(sessionId: string, title: string) {
    return this.http.patch<{ title: string }>(`${API_BASE}/chat/sessions/${sessionId}`, { title });
  }

  remove(sessionId: string) {
    return this.http.delete<void>(`${API_BASE}/chat/sessions/${sessionId}`);
  }
}
