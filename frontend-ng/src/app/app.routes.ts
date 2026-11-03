import { Routes } from '@angular/router';
import { authGuard, guestGuard } from './core/auth.http';
import { ChatComponent } from './pages/chat.component';
import { LoginComponent } from './pages/login.component';
import { RegisterComponent } from './pages/register.component';

export const routes: Routes = [
  { path: 'login', component: LoginComponent, canActivate: [guestGuard], title: 'Sign in - Plan Assistant' },
  { path: 'register', component: RegisterComponent, canActivate: [guestGuard], title: 'Register - Plan Assistant' },
  { path: 'chat', component: ChatComponent, canActivate: [authGuard], title: 'Plan Assistant' },
  { path: '**', redirectTo: 'chat' },
];
