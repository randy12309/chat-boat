import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { map } from 'rxjs';
import { API_BASE } from './api';

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

  home() {
    return this.http.get<HomeData>(`${API_BASE}/home`);
  }

  send(message: string, context: unknown, sessionId: string) {
    return this.http
      .post<{ reply: string }>(`${API_BASE}/chat`, { message, context: context ?? null, session_id: sessionId })
      .pipe(map((res) => res.reply));
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
