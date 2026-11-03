import { HttpClient } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { Observable, tap } from 'rxjs';
import { API_BASE } from './api';

const TOKEN_KEY = 'pa_token';
const USER_KEY = 'pa_user';

export interface AuthResponse {
  access_token: string;
  username: string;
  expires_in: number;
}

export interface RegisterRequest {
  username: string;
  email: string;
  display_name: string;
  password: string;
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);

  readonly user = signal<string | null>(sessionStorage.getItem(USER_KEY));

  token(): string | null {
    return sessionStorage.getItem(TOKEN_KEY);
  }

  isLoggedIn(): boolean {
    return !!this.token();
  }

  login(username: string, password: string): Observable<AuthResponse> {
    return this.http
      .post<AuthResponse>(`${API_BASE}/auth/login`, { username, password })
      .pipe(tap((res) => this.store(res)));
  }

  register(body: RegisterRequest): Observable<AuthResponse> {
    return this.http.post<AuthResponse>(`${API_BASE}/auth/register`, body).pipe(tap((res) => this.store(res)));
  }

  logout(): void {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(USER_KEY);
    this.user.set(null);
    this.router.navigateByUrl('/login');
  }

  private store(res: AuthResponse): void {
    sessionStorage.setItem(TOKEN_KEY, res.access_token);
    sessionStorage.setItem(USER_KEY, res.username);
    this.user.set(res.username);
  }
}
