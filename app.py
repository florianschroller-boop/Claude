import os
import re
from datetime import datetime, timezone
from functools import wraps

import markdown
from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_from_directory, url_for)
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from flask_sqlalchemy import SQLAlchemy
from slugify import slugify
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-prod')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///ticketsystem.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'txt', 'doc', 'docx', 'xlsx', 'zip'}

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Bitte melde dich an, um diese Seite zu sehen.'
login_manager.login_message_category = 'warning'

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

team_members = db.Table(
    'team_members',
    db.Column('team_id', db.Integer, db.ForeignKey('team.id')),
    db.Column('user_id', db.Integer, db.ForeignKey('user.id')),
)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256))
    role = db.Column(db.String(20), default='user')  # admin, agent, user
    full_name = db.Column(db.String(120))
    department = db.Column(db.String(80))
    avatar = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    is_active = db.Column(db.Boolean, default=True)

    tickets_reported = db.relationship('Ticket', foreign_keys='Ticket.reporter_id', backref='reporter', lazy='dynamic')
    tickets_assigned = db.relationship('Ticket', foreign_keys='Ticket.assignee_id', backref='assignee', lazy='dynamic')
    comments = db.relationship('TicketComment', backref='author', lazy='dynamic')
    assets_assigned = db.relationship('Asset', foreign_keys='Asset.assigned_to_id', backref='assigned_to', lazy='dynamic')
    doc_pages = db.relationship('DocPage', backref='author', lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        return self.role == 'admin'

    def is_agent_or_admin(self):
        return self.role in ('admin', 'agent')

    @property
    def display_name(self):
        return self.full_name or self.username

    @property
    def initials(self):
        name = self.display_name
        parts = name.split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        return name[:2].upper()


class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    members = db.relationship('User', secondary=team_members, backref='teams')
    tickets = db.relationship('Ticket', backref='team', lazy='dynamic')


class Ticket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(30), default='open')
    # open | in_progress | pending | resolved | closed
    priority = db.Column(db.String(20), default='medium')
    # low | medium | high | critical
    category = db.Column(db.String(80))
    tags = db.Column(db.String(200))
    reporter_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    assignee_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    asset_id = db.Column(db.Integer, db.ForeignKey('asset.id'))
    due_date = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))
    closed_at = db.Column(db.DateTime)

    comments = db.relationship('TicketComment', backref='ticket', lazy='dynamic',
                               cascade='all, delete-orphan')
    attachments = db.relationship('Attachment', backref='ticket', lazy='dynamic',
                                  cascade='all, delete-orphan')

    STATUS_LABELS = {
        'open': ('Offen', 'secondary'),
        'in_progress': ('In Bearbeitung', 'primary'),
        'pending': ('Wartend', 'warning'),
        'resolved': ('Gelöst', 'success'),
        'closed': ('Geschlossen', 'dark'),
    }
    PRIORITY_LABELS = {
        'low': ('Niedrig', 'info'),
        'medium': ('Mittel', 'secondary'),
        'high': ('Hoch', 'warning'),
        'critical': ('Kritisch', 'danger'),
    }

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, (self.status, 'secondary'))

    @property
    def priority_label(self):
        return self.PRIORITY_LABELS.get(self.priority, (self.priority, 'secondary'))

    @property
    def tag_list(self):
        if self.tags:
            return [t.strip() for t in self.tags.split(',') if t.strip()]
        return []


class TicketComment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_internal = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Attachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'))
    filename = db.Column(db.String(200))
    original_name = db.Column(db.String(200))
    file_size = db.Column(db.Integer)
    uploaded_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    uploader = db.relationship('User')


class Asset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    asset_tag = db.Column(db.String(80), unique=True)
    asset_type = db.Column(db.String(50))  # hardware | software | license | network | other
    manufacturer = db.Column(db.String(100))
    model = db.Column(db.String(100))
    serial_number = db.Column(db.String(100))
    status = db.Column(db.String(30), default='active')
    # active | inactive | maintenance | retired | lost
    location = db.Column(db.String(200))
    department = db.Column(db.String(100))
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    purchase_date = db.Column(db.Date)
    purchase_price = db.Column(db.Float)
    warranty_until = db.Column(db.Date)
    notes = db.Column(db.Text)
    ip_address = db.Column(db.String(50))
    mac_address = db.Column(db.String(50))
    os_version = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    tickets = db.relationship('Ticket', backref='asset', lazy='dynamic')

    STATUS_LABELS = {
        'active': ('Aktiv', 'success'),
        'inactive': ('Inaktiv', 'secondary'),
        'maintenance': ('Wartung', 'warning'),
        'retired': ('Ausgemustert', 'dark'),
        'lost': ('Verloren', 'danger'),
    }
    TYPE_LABELS = {
        'hardware': ('Hardware', 'primary'),
        'software': ('Software', 'info'),
        'license': ('Lizenz', 'success'),
        'network': ('Netzwerk', 'warning'),
        'other': ('Sonstiges', 'secondary'),
    }

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, (self.status, 'secondary'))

    @property
    def type_label(self):
        return self.TYPE_LABELS.get(self.asset_type, (self.asset_type or 'Unbekannt', 'secondary'))

    @property
    def is_warranty_expired(self):
        if self.warranty_until:
            return self.warranty_until < datetime.now(timezone.utc).date()
        return None


class DocPage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    namespace = db.Column(db.String(200), default='')
    slug = db.Column(db.String(200), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, default='')
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))
    is_public = db.Column(db.Boolean, default=True)

    revisions = db.relationship('DocRevision', backref='page', lazy='dynamic',
                                cascade='all, delete-orphan',
                                order_by='DocRevision.created_at.desc()')

    __table_args__ = (db.UniqueConstraint('namespace', 'slug'),)

    @property
    def full_path(self):
        if self.namespace:
            return f"{self.namespace}/{self.slug}"
        return self.slug

    @property
    def rendered_content(self):
        md = markdown.Markdown(extensions=[
            'extra', 'codehilite', 'toc', 'tables', 'fenced_code',
            'nl2br', 'sane_lists',
        ])
        return md.convert(self.content or '')

    @property
    def toc(self):
        md = markdown.Markdown(extensions=['toc'])
        md.convert(self.content or '')
        return md.toc

    @property
    def namespace_parts(self):
        if self.namespace:
            return self.namespace.split(':')
        return []


class DocRevision(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    page_id = db.Column(db.Integer, db.ForeignKey('doc_page.id'), nullable=False)
    content = db.Column(db.Text)
    title = db.Column(db.String(200))
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    comment = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    author = db.relationship('User')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin():
            abort(403)
        return f(*args, **kwargs)
    return decorated


def agent_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_agent_or_admin():
            abort(403)
        return f(*args, **kwargs)
    return decorated


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def save_upload(file):
    filename = secure_filename(file.filename)
    ts = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')
    unique_name = f"{ts}_{filename}"
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_name))
    return unique_name, filename, os.path.getsize(
        os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
    )


@app.template_filter('markdown')
def markdown_filter(text):
    if not text:
        return ''
    md = markdown.Markdown(extensions=['extra', 'codehilite', 'nl2br', 'sane_lists'])
    return md.convert(text)


@app.template_filter('timeago')
def timeago_filter(dt):
    if not dt:
        return ''
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return 'gerade eben'
    if seconds < 3600:
        m = seconds // 60
        return f'vor {m} Minute{"n" if m > 1 else ""}'
    if seconds < 86400:
        h = seconds // 3600
        return f'vor {h} Stunde{"n" if h > 1 else ""}'
    d = seconds // 86400
    if d < 30:
        return f'vor {d} Tag{"en" if d > 1 else ""}'
    return dt.strftime('%d.%m.%Y')


@app.context_processor
def inject_globals():
    if current_user.is_authenticated:
        open_tickets = Ticket.query.filter(
            Ticket.status.in_(['open', 'in_progress'])
        ).count()
        my_tickets = Ticket.query.filter(
            Ticket.assignee_id == current_user.id,
            Ticket.status.in_(['open', 'in_progress'])
        ).count()
    else:
        open_tickets = 0
        my_tickets = 0
    return dict(open_tickets=open_tickets, my_tickets=my_tickets)


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter(
            (User.username == username) | (User.email == username)
        ).first()
        if user and user.check_password(password) and user.is_active:
            login_user(user, remember=request.form.get('remember'))
            next_page = request.args.get('next')
            flash(f'Willkommen zurück, {user.display_name}!', 'success')
            return redirect(next_page or url_for('dashboard'))
        flash('Ungültige Anmeldedaten.', 'danger')
    return render_template('auth/login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Du wurdest abgemeldet.', 'info')
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    # Only allow if no users exist yet (first-time setup) or admin
    user_count = User.query.count()
    if user_count > 0 and (not current_user.is_authenticated or not current_user.is_admin()):
        flash('Registrierung ist nur für Administratoren möglich.', 'warning')
        return redirect(url_for('login'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        full_name = request.form.get('full_name', '').strip()
        if User.query.filter_by(username=username).first():
            flash('Benutzername bereits vergeben.', 'danger')
        elif User.query.filter_by(email=email).first():
            flash('E-Mail bereits registriert.', 'danger')
        elif len(password) < 6:
            flash('Passwort muss mindestens 6 Zeichen lang sein.', 'danger')
        else:
            role = 'admin' if user_count == 0 else 'user'
            user = User(username=username, email=email, full_name=full_name, role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash(f'Konto erstellt! Willkommen, {user.display_name}!', 'success')
            return redirect(url_for('dashboard'))
    return render_template('auth/register.html')


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route('/')
@login_required
def dashboard():
    stats = {
        'tickets_open': Ticket.query.filter_by(status='open').count(),
        'tickets_in_progress': Ticket.query.filter_by(status='in_progress').count(),
        'tickets_resolved': Ticket.query.filter_by(status='resolved').count(),
        'tickets_my': Ticket.query.filter_by(assignee_id=current_user.id).filter(
            Ticket.status.in_(['open', 'in_progress'])).count(),
        'assets_total': Asset.query.count(),
        'assets_active': Asset.query.filter_by(status='active').count(),
        'assets_maintenance': Asset.query.filter_by(status='maintenance').count(),
        'docs_total': DocPage.query.count(),
    }
    recent_tickets = Ticket.query.order_by(Ticket.created_at.desc()).limit(8).all()
    assigned_tickets = Ticket.query.filter_by(assignee_id=current_user.id).filter(
        Ticket.status.in_(['open', 'in_progress'])
    ).order_by(Ticket.priority.desc()).limit(5).all()
    critical_tickets = Ticket.query.filter_by(priority='critical').filter(
        Ticket.status.in_(['open', 'in_progress'])
    ).order_by(Ticket.created_at.desc()).limit(5).all()
    return render_template('dashboard.html', stats=stats, recent_tickets=recent_tickets,
                           assigned_tickets=assigned_tickets, critical_tickets=critical_tickets)


# ---------------------------------------------------------------------------
# Ticket routes
# ---------------------------------------------------------------------------

@app.route('/tickets')
@login_required
def ticket_list():
    query = Ticket.query
    status = request.args.get('status', '')
    priority = request.args.get('priority', '')
    assignee = request.args.get('assignee', '')
    search = request.args.get('search', '')
    category = request.args.get('category', '')
    mine = request.args.get('mine', '')

    if status:
        query = query.filter_by(status=status)
    if priority:
        query = query.filter_by(priority=priority)
    if assignee:
        query = query.filter_by(assignee_id=int(assignee))
    if category:
        query = query.filter_by(category=category)
    if mine:
        query = query.filter_by(assignee_id=current_user.id)
    if search:
        query = query.filter(
            Ticket.title.ilike(f'%{search}%') | Ticket.description.ilike(f'%{search}%')
        )

    sort = request.args.get('sort', 'created_at_desc')
    sort_map = {
        'created_at_desc': Ticket.created_at.desc(),
        'created_at_asc': Ticket.created_at.asc(),
        'updated_at_desc': Ticket.updated_at.desc(),
        'priority_desc': Ticket.priority.desc(),
    }
    query = query.order_by(sort_map.get(sort, Ticket.created_at.desc()))

    page = request.args.get('page', 1, type=int)
    tickets = query.paginate(page=page, per_page=20, error_out=False)

    agents = User.query.filter(User.role.in_(['admin', 'agent'])).all()
    categories = db.session.query(Ticket.category).filter(
        Ticket.category.isnot(None), Ticket.category != ''
    ).distinct().all()
    categories = [c[0] for c in categories]

    return render_template('tickets/list.html', tickets=tickets, agents=agents,
                           categories=categories,
                           filters=dict(status=status, priority=priority,
                                        assignee=assignee, search=search,
                                        category=category, mine=mine, sort=sort))


@app.route('/tickets/create', methods=['GET', 'POST'])
@login_required
def ticket_create():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if not title:
            flash('Titel ist erforderlich.', 'danger')
            return redirect(request.url)

        ticket = Ticket(
            title=title,
            description=request.form.get('description', ''),
            status='open',
            priority=request.form.get('priority', 'medium'),
            category=request.form.get('category', '').strip(),
            tags=request.form.get('tags', '').strip(),
            reporter_id=current_user.id,
        )
        assignee_id = request.form.get('assignee_id')
        if assignee_id:
            ticket.assignee_id = int(assignee_id)
        asset_id = request.form.get('asset_id')
        if asset_id:
            ticket.asset_id = int(asset_id)
        team_id = request.form.get('team_id')
        if team_id:
            ticket.team_id = int(team_id)
        due_date = request.form.get('due_date')
        if due_date:
            ticket.due_date = datetime.strptime(due_date, '%Y-%m-%d')

        db.session.add(ticket)
        db.session.flush()

        # Handle attachments
        files = request.files.getlist('attachments')
        for f in files:
            if f and f.filename and allowed_file(f.filename):
                unique_name, orig_name, size = save_upload(f)
                att = Attachment(ticket_id=ticket.id, filename=unique_name,
                                 original_name=orig_name, file_size=size,
                                 uploaded_by=current_user.id)
                db.session.add(att)

        db.session.commit()
        flash(f'Ticket #{ticket.id} wurde erstellt.', 'success')
        return redirect(url_for('ticket_detail', ticket_id=ticket.id))

    agents = User.query.filter(User.role.in_(['admin', 'agent'])).all()
    assets = Asset.query.filter_by(status='active').order_by(Asset.name).all()
    teams = Team.query.order_by(Team.name).all()
    return render_template('tickets/create.html', agents=agents, assets=assets, teams=teams)


@app.route('/tickets/<int:ticket_id>')
@login_required
def ticket_detail(ticket_id):
    ticket = db.get_or_404(Ticket, ticket_id)
    comments = ticket.comments.order_by(TicketComment.created_at.asc()).all()
    agents = User.query.filter(User.role.in_(['admin', 'agent'])).all()
    teams = Team.query.all()
    assets = Asset.query.filter_by(status='active').order_by(Asset.name).all()
    return render_template('tickets/detail.html', ticket=ticket, comments=comments,
                           agents=agents, teams=teams, assets=assets)


@app.route('/tickets/<int:ticket_id>/edit', methods=['GET', 'POST'])
@login_required
def ticket_edit(ticket_id):
    ticket = db.get_or_404(Ticket, ticket_id)
    if not current_user.is_agent_or_admin() and ticket.reporter_id != current_user.id:
        abort(403)

    if request.method == 'POST':
        ticket.title = request.form.get('title', '').strip()
        ticket.description = request.form.get('description', '')
        ticket.status = request.form.get('status', ticket.status)
        ticket.priority = request.form.get('priority', ticket.priority)
        ticket.category = request.form.get('category', '').strip()
        ticket.tags = request.form.get('tags', '').strip()
        ticket.updated_at = datetime.now(timezone.utc)

        assignee_id = request.form.get('assignee_id')
        ticket.assignee_id = int(assignee_id) if assignee_id else None
        asset_id = request.form.get('asset_id')
        ticket.asset_id = int(asset_id) if asset_id else None
        team_id = request.form.get('team_id')
        ticket.team_id = int(team_id) if team_id else None
        due_date = request.form.get('due_date')
        ticket.due_date = datetime.strptime(due_date, '%Y-%m-%d') if due_date else None

        if ticket.status in ('resolved', 'closed') and not ticket.closed_at:
            ticket.closed_at = datetime.now(timezone.utc)

        files = request.files.getlist('attachments')
        for f in files:
            if f and f.filename and allowed_file(f.filename):
                unique_name, orig_name, size = save_upload(f)
                att = Attachment(ticket_id=ticket.id, filename=unique_name,
                                 original_name=orig_name, file_size=size,
                                 uploaded_by=current_user.id)
                db.session.add(att)

        db.session.commit()
        flash('Ticket aktualisiert.', 'success')
        return redirect(url_for('ticket_detail', ticket_id=ticket.id))

    agents = User.query.filter(User.role.in_(['admin', 'agent'])).all()
    assets = Asset.query.order_by(Asset.name).all()
    teams = Team.query.all()
    return render_template('tickets/edit.html', ticket=ticket, agents=agents,
                           assets=assets, teams=teams)


@app.route('/tickets/<int:ticket_id>/comment', methods=['POST'])
@login_required
def ticket_comment(ticket_id):
    ticket = db.get_or_404(Ticket, ticket_id)
    content = request.form.get('content', '').strip()
    if not content:
        flash('Kommentar darf nicht leer sein.', 'danger')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    is_internal = bool(request.form.get('is_internal')) and current_user.is_agent_or_admin()
    comment = TicketComment(ticket_id=ticket.id, user_id=current_user.id,
                            content=content, is_internal=is_internal)
    db.session.add(comment)
    ticket.updated_at = datetime.now(timezone.utc)

    # Quick status change from comment form
    new_status = request.form.get('new_status')
    if new_status and current_user.is_agent_or_admin():
        ticket.status = new_status
        if new_status in ('resolved', 'closed') and not ticket.closed_at:
            ticket.closed_at = datetime.now(timezone.utc)

    db.session.commit()
    flash('Kommentar hinzugefügt.', 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))


@app.route('/tickets/<int:ticket_id>/delete', methods=['POST'])
@login_required
@admin_required
def ticket_delete(ticket_id):
    ticket = db.get_or_404(Ticket, ticket_id)
    db.session.delete(ticket)
    db.session.commit()
    flash(f'Ticket #{ticket_id} gelöscht.', 'success')
    return redirect(url_for('ticket_list'))


@app.route('/attachments/<filename>')
@login_required
def download_attachment(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# ---------------------------------------------------------------------------
# Asset routes
# ---------------------------------------------------------------------------

@app.route('/assets')
@login_required
def asset_list():
    query = Asset.query
    asset_type = request.args.get('type', '')
    status = request.args.get('status', '')
    search = request.args.get('search', '')
    department = request.args.get('department', '')

    if asset_type:
        query = query.filter_by(asset_type=asset_type)
    if status:
        query = query.filter_by(status=status)
    if department:
        query = query.filter_by(department=department)
    if search:
        query = query.filter(
            Asset.name.ilike(f'%{search}%') |
            Asset.serial_number.ilike(f'%{search}%') |
            Asset.asset_tag.ilike(f'%{search}%') |
            Asset.manufacturer.ilike(f'%{search}%')
        )

    sort = request.args.get('sort', 'name_asc')
    sort_map = {
        'name_asc': Asset.name.asc(),
        'name_desc': Asset.name.desc(),
        'created_at_desc': Asset.created_at.desc(),
        'type_asc': Asset.asset_type.asc(),
    }
    query = query.order_by(sort_map.get(sort, Asset.name.asc()))

    page = request.args.get('page', 1, type=int)
    assets = query.paginate(page=page, per_page=20, error_out=False)

    departments = db.session.query(Asset.department).filter(
        Asset.department.isnot(None), Asset.department != ''
    ).distinct().all()
    departments = [d[0] for d in departments]

    stats = {
        'total': Asset.query.count(),
        'active': Asset.query.filter_by(status='active').count(),
        'maintenance': Asset.query.filter_by(status='maintenance').count(),
        'retired': Asset.query.filter_by(status='retired').count(),
    }

    return render_template('assets/list.html', assets=assets, stats=stats,
                           departments=departments,
                           filters=dict(type=asset_type, status=status,
                                        search=search, department=department, sort=sort))


@app.route('/assets/create', methods=['GET', 'POST'])
@login_required
@agent_required
def asset_create():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Name ist erforderlich.', 'danger')
            return redirect(request.url)

        asset = Asset(
            name=name,
            asset_tag=request.form.get('asset_tag', '').strip() or None,
            asset_type=request.form.get('asset_type', 'hardware'),
            manufacturer=request.form.get('manufacturer', '').strip(),
            model=request.form.get('model', '').strip(),
            serial_number=request.form.get('serial_number', '').strip(),
            status=request.form.get('status', 'active'),
            location=request.form.get('location', '').strip(),
            department=request.form.get('department', '').strip(),
            notes=request.form.get('notes', '').strip(),
            ip_address=request.form.get('ip_address', '').strip(),
            mac_address=request.form.get('mac_address', '').strip(),
            os_version=request.form.get('os_version', '').strip(),
        )
        assigned_to_id = request.form.get('assigned_to_id')
        if assigned_to_id:
            asset.assigned_to_id = int(assigned_to_id)
        purchase_date = request.form.get('purchase_date')
        if purchase_date:
            asset.purchase_date = datetime.strptime(purchase_date, '%Y-%m-%d').date()
        warranty_until = request.form.get('warranty_until')
        if warranty_until:
            asset.warranty_until = datetime.strptime(warranty_until, '%Y-%m-%d').date()
        purchase_price = request.form.get('purchase_price')
        if purchase_price:
            try:
                asset.purchase_price = float(purchase_price.replace(',', '.'))
            except ValueError:
                pass

        db.session.add(asset)
        db.session.commit()
        flash(f'Asset "{asset.name}" wurde erstellt.', 'success')
        return redirect(url_for('asset_detail', asset_id=asset.id))

    users = User.query.filter_by(is_active=True).order_by(User.username).all()
    return render_template('assets/create.html', users=users)


@app.route('/assets/<int:asset_id>')
@login_required
def asset_detail(asset_id):
    asset = db.get_or_404(Asset, asset_id)
    tickets = asset.tickets.order_by(Ticket.created_at.desc()).limit(10).all()
    return render_template('assets/detail.html', asset=asset, tickets=tickets)


@app.route('/assets/<int:asset_id>/edit', methods=['GET', 'POST'])
@login_required
@agent_required
def asset_edit(asset_id):
    asset = db.get_or_404(Asset, asset_id)
    if request.method == 'POST':
        asset.name = request.form.get('name', '').strip()
        asset.asset_tag = request.form.get('asset_tag', '').strip() or None
        asset.asset_type = request.form.get('asset_type', 'hardware')
        asset.manufacturer = request.form.get('manufacturer', '').strip()
        asset.model = request.form.get('model', '').strip()
        asset.serial_number = request.form.get('serial_number', '').strip()
        asset.status = request.form.get('status', 'active')
        asset.location = request.form.get('location', '').strip()
        asset.department = request.form.get('department', '').strip()
        asset.notes = request.form.get('notes', '').strip()
        asset.ip_address = request.form.get('ip_address', '').strip()
        asset.mac_address = request.form.get('mac_address', '').strip()
        asset.os_version = request.form.get('os_version', '').strip()
        asset.updated_at = datetime.now(timezone.utc)

        assigned_to_id = request.form.get('assigned_to_id')
        asset.assigned_to_id = int(assigned_to_id) if assigned_to_id else None
        purchase_date = request.form.get('purchase_date')
        asset.purchase_date = datetime.strptime(purchase_date, '%Y-%m-%d').date() if purchase_date else None
        warranty_until = request.form.get('warranty_until')
        asset.warranty_until = datetime.strptime(warranty_until, '%Y-%m-%d').date() if warranty_until else None
        purchase_price = request.form.get('purchase_price')
        if purchase_price:
            try:
                asset.purchase_price = float(purchase_price.replace(',', '.'))
            except ValueError:
                asset.purchase_price = None
        else:
            asset.purchase_price = None

        db.session.commit()
        flash('Asset aktualisiert.', 'success')
        return redirect(url_for('asset_detail', asset_id=asset.id))

    users = User.query.filter_by(is_active=True).order_by(User.username).all()
    return render_template('assets/edit.html', asset=asset, users=users)


@app.route('/assets/<int:asset_id>/delete', methods=['POST'])
@login_required
@admin_required
def asset_delete(asset_id):
    asset = db.get_or_404(Asset, asset_id)
    name = asset.name
    db.session.delete(asset)
    db.session.commit()
    flash(f'Asset "{name}" gelöscht.', 'success')
    return redirect(url_for('asset_list'))


# ---------------------------------------------------------------------------
# Documentation routes
# ---------------------------------------------------------------------------

@app.route('/docs')
@login_required
def doc_list():
    namespace = request.args.get('ns', '')
    search = request.args.get('search', '')

    query = DocPage.query
    if namespace:
        query = query.filter_by(namespace=namespace)
    if search:
        query = query.filter(
            DocPage.title.ilike(f'%{search}%') | DocPage.content.ilike(f'%{search}%')
        )
    pages = query.order_by(DocPage.namespace, DocPage.title).all()

    # Build namespace tree
    namespaces = db.session.query(DocPage.namespace).filter(
        DocPage.namespace.isnot(None)
    ).distinct().order_by(DocPage.namespace).all()
    namespaces = [n[0] for n in namespaces if n[0]]

    recent = DocPage.query.order_by(DocPage.updated_at.desc()).limit(10).all()
    return render_template('docs/list.html', pages=pages, namespaces=namespaces,
                           recent=recent, current_ns=namespace, search=search)


@app.route('/docs/create', methods=['GET', 'POST'])
@login_required
def doc_create():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        namespace = request.form.get('namespace', '').strip().lower().replace(' ', '_')
        namespace = re.sub(r'[^a-z0-9_:]', '', namespace)
        slug_input = request.form.get('slug', '').strip()
        slug = slugify(slug_input or title, separator='_', lowercase=True)

        if not title:
            flash('Titel ist erforderlich.', 'danger')
            return redirect(request.url)

        existing = DocPage.query.filter_by(namespace=namespace, slug=slug).first()
        if existing:
            flash('Eine Seite mit diesem Namen/Namespace existiert bereits.', 'danger')
            return redirect(request.url)

        page = DocPage(
            namespace=namespace,
            slug=slug,
            title=title,
            content=request.form.get('content', ''),
            author_id=current_user.id,
            is_public=bool(request.form.get('is_public', True)),
        )
        db.session.add(page)
        db.session.flush()

        revision = DocRevision(
            page_id=page.id,
            content=page.content,
            title=page.title,
            author_id=current_user.id,
            comment='Seite erstellt',
        )
        db.session.add(revision)
        db.session.commit()
        flash(f'Seite "{title}" erstellt.', 'success')
        return redirect(url_for('doc_view', path=page.full_path))

    namespaces = db.session.query(DocPage.namespace).filter(
        DocPage.namespace != ''
    ).distinct().all()
    namespaces = [n[0] for n in namespaces if n[0]]
    prefill_ns = request.args.get('ns', '')
    return render_template('docs/create.html', namespaces=namespaces, prefill_ns=prefill_ns)


@app.route('/docs/view/<path:path>')
@login_required
def doc_view(path):
    parts = path.rsplit('/', 1)
    if len(parts) == 2:
        namespace, slug = parts
    else:
        namespace, slug = '', parts[0]

    page = DocPage.query.filter_by(namespace=namespace, slug=slug).first_or_404()
    return render_template('docs/view.html', page=page)


@app.route('/docs/edit/<path:path>', methods=['GET', 'POST'])
@login_required
def doc_edit(path):
    parts = path.rsplit('/', 1)
    namespace = parts[0] if len(parts) == 2 else ''
    slug = parts[-1]
    page = DocPage.query.filter_by(namespace=namespace, slug=slug).first_or_404()

    if request.method == 'POST':
        old_content = page.content
        page.title = request.form.get('title', '').strip()
        page.content = request.form.get('content', '')
        page.is_public = bool(request.form.get('is_public'))
        page.updated_at = datetime.now(timezone.utc)

        if page.content != old_content or page.title != request.form.get('title'):
            revision = DocRevision(
                page_id=page.id,
                content=page.content,
                title=page.title,
                author_id=current_user.id,
                comment=request.form.get('comment', '').strip() or 'Bearbeitet',
            )
            db.session.add(revision)

        db.session.commit()
        flash('Seite gespeichert.', 'success')
        return redirect(url_for('doc_view', path=page.full_path))

    return render_template('docs/edit.html', page=page)


@app.route('/docs/history/<path:path>')
@login_required
def doc_history(path):
    parts = path.rsplit('/', 1)
    namespace = parts[0] if len(parts) == 2 else ''
    slug = parts[-1]
    page = DocPage.query.filter_by(namespace=namespace, slug=slug).first_or_404()
    revisions = page.revisions.order_by(DocRevision.created_at.desc()).all()
    return render_template('docs/history.html', page=page, revisions=revisions)


@app.route('/docs/revision/<int:rev_id>')
@login_required
def doc_revision(rev_id):
    revision = db.get_or_404(DocRevision, rev_id)
    page = revision.page
    return render_template('docs/revision.html', revision=revision, page=page)


@app.route('/docs/delete/<path:path>', methods=['POST'])
@login_required
@admin_required
def doc_delete(path):
    parts = path.rsplit('/', 1)
    namespace = parts[0] if len(parts) == 2 else ''
    slug = parts[-1]
    page = DocPage.query.filter_by(namespace=namespace, slug=slug).first_or_404()
    title = page.title
    db.session.delete(page)
    db.session.commit()
    flash(f'Seite "{title}" gelöscht.', 'success')
    return redirect(url_for('doc_list'))


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@app.route('/admin')
@login_required
@admin_required
def admin_panel():
    users = User.query.order_by(User.created_at.desc()).all()
    teams = Team.query.order_by(Team.name).all()
    return render_template('admin/panel.html', users=users, teams=teams)


@app.route('/admin/users/<int:user_id>/edit', methods=['POST'])
@login_required
@admin_required
def admin_user_edit(user_id):
    user = db.get_or_404(User, user_id)
    user.role = request.form.get('role', user.role)
    user.is_active = bool(request.form.get('is_active'))
    user.full_name = request.form.get('full_name', '').strip()
    user.department = request.form.get('department', '').strip()
    new_password = request.form.get('new_password', '')
    if new_password and len(new_password) >= 6:
        user.set_password(new_password)
    db.session.commit()
    flash(f'Benutzer {user.username} aktualisiert.', 'success')
    return redirect(url_for('admin_panel'))


@app.route('/admin/users/create', methods=['POST'])
@login_required
@admin_required
def admin_user_create():
    username = request.form.get('username', '').strip()
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '')
    if not username or not email or not password:
        flash('Alle Felder sind erforderlich.', 'danger')
        return redirect(url_for('admin_panel'))
    if User.query.filter_by(username=username).first():
        flash('Benutzername bereits vergeben.', 'danger')
        return redirect(url_for('admin_panel'))
    user = User(username=username, email=email,
                full_name=request.form.get('full_name', '').strip(),
                role=request.form.get('role', 'user'),
                department=request.form.get('department', '').strip())
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash(f'Benutzer {username} erstellt.', 'success')
    return redirect(url_for('admin_panel'))


@app.route('/admin/teams/create', methods=['POST'])
@login_required
@admin_required
def admin_team_create():
    name = request.form.get('name', '').strip()
    if not name:
        flash('Team-Name erforderlich.', 'danger')
        return redirect(url_for('admin_panel'))
    team = Team(name=name, description=request.form.get('description', '').strip())
    db.session.add(team)
    db.session.commit()
    flash(f'Team "{name}" erstellt.', 'success')
    return redirect(url_for('admin_panel'))


@app.route('/admin/teams/<int:team_id>/members', methods=['POST'])
@login_required
@admin_required
def admin_team_members(team_id):
    team = db.get_or_404(Team, team_id)
    user_ids = request.form.getlist('user_ids')
    team.members = []
    for uid in user_ids:
        user = db.session.get(User, int(uid))
        if user:
            team.members.append(user)
    db.session.commit()
    flash(f'Team "{team.name}" aktualisiert.', 'success')
    return redirect(url_for('admin_panel'))


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        current_user.full_name = request.form.get('full_name', '').strip()
        current_user.department = request.form.get('department', '').strip()
        new_email = request.form.get('email', '').strip()
        if new_email and new_email != current_user.email:
            if User.query.filter_by(email=new_email).first():
                flash('E-Mail bereits vergeben.', 'danger')
                return redirect(request.url)
            current_user.email = new_email
        old_pw = request.form.get('old_password', '')
        new_pw = request.form.get('new_password', '')
        if old_pw and new_pw:
            if not current_user.check_password(old_pw):
                flash('Aktuelles Passwort falsch.', 'danger')
                return redirect(request.url)
            if len(new_pw) < 6:
                flash('Neues Passwort muss mind. 6 Zeichen haben.', 'danger')
                return redirect(request.url)
            current_user.set_password(new_pw)
            flash('Passwort geändert.', 'success')
        db.session.commit()
        flash('Profil gespeichert.', 'success')
        return redirect(url_for('profile'))
    return render_template('auth/profile.html')


# ---------------------------------------------------------------------------
# Init DB & run
# ---------------------------------------------------------------------------

def init_db():
    with app.app_context():
        db.create_all()
        # Create demo data if empty
        if User.query.count() == 0:
            admin = User(username='admin', email='admin@example.com',
                         full_name='Administrator', role='admin', department='IT')
            admin.set_password('admin123')
            db.session.add(admin)

            agent = User(username='agent1', email='agent1@example.com',
                         full_name='Max Mustermann', role='agent', department='IT-Support')
            agent.set_password('agent123')
            db.session.add(agent)

            user = User(username='user1', email='user1@example.com',
                        full_name='Erika Muster', role='user', department='Vertrieb')
            user.set_password('user123')
            db.session.add(user)
            db.session.flush()

            team = Team(name='IT-Support', description='Internes IT-Support Team')
            team.members = [admin, agent]
            db.session.add(team)

            # Demo assets
            assets_data = [
                Asset(name='ThinkPad X1 Carbon', asset_type='hardware', manufacturer='Lenovo',
                      model='X1 Carbon Gen 11', serial_number='SN-001-2024', status='active',
                      location='Büro 1.01', department='IT', assigned_to_id=agent.id,
                      asset_tag='IT-001', os_version='Windows 11 Pro'),
                Asset(name='Microsoft Office 365', asset_type='license', manufacturer='Microsoft',
                      serial_number='MS-365-ENT', status='active', department='IT',
                      notes='Enterprise Lizenz, 50 Seats'),
                Asset(name='Cisco Switch 24-Port', asset_type='network', manufacturer='Cisco',
                      model='Catalyst 2960', serial_number='NET-SW-001', status='active',
                      location='Serverraum', department='IT', asset_tag='NET-001'),
            ]
            for a in assets_data:
                db.session.add(a)
            db.session.flush()

            # Demo tickets
            tickets_data = [
                Ticket(title='VPN Verbindung bricht ab', description='VPN trennt sich alle 30 Minuten.',
                       status='open', priority='high', category='Netzwerk',
                       reporter_id=user.id, assignee_id=agent.id, team_id=team.id),
                Ticket(title='Drucker druckt nicht', description='HP Drucker im 2. OG reagiert nicht.',
                       status='in_progress', priority='medium', category='Hardware',
                       reporter_id=user.id, assignee_id=agent.id),
                Ticket(title='E-Mail Setup neuer Mitarbeiter',
                       description='Neuer Mitarbeiter braucht E-Mail-Konto.',
                       status='open', priority='low', category='E-Mail', reporter_id=admin.id),
            ]
            for t in tickets_data:
                db.session.add(t)

            # Demo docs
            doc1 = DocPage(namespace='', slug='start', title='Willkommen',
                           content='# Willkommen im Wiki\n\nDies ist das interne Wissensmanagementsystem.\n\n## Bereiche\n\n* [[it:start|IT-Dokumentation]]\n* [[howto:start|Anleitungen]]\n\n## Schnellzugriff\n\nNutze die Seitenleiste zur Navigation.',
                           author_id=admin.id)
            doc2 = DocPage(namespace='it', slug='start', title='IT-Dokumentation',
                           content='# IT-Dokumentation\n\n## Netzwerk\n\n```\nGateway: 192.168.1.1\nDNS: 8.8.8.8\nVPN: vpn.example.com\n```\n\n## Server\n\n| Server | IP | Rolle |\n|--------|-----|-------|\n| srv01 | 192.168.1.10 | Fileserver |\n| srv02 | 192.168.1.11 | Mailserver |\n\n## Passwort-Richtlinie\n\nPasswörter müssen:\n- Mindestens 12 Zeichen lang sein\n- Groß- und Kleinbuchstaben enthalten\n- Mindestens eine Zahl enthalten',
                           author_id=admin.id)
            doc3 = DocPage(namespace='howto', slug='vpn', title='VPN Einrichten',
                           content='# VPN Einrichten\n\n## Windows\n\n1. Cisco AnyConnect herunterladen\n2. VPN-Adresse eingeben: `vpn.example.com`\n3. Mit Benutzername und Passwort anmelden\n\n## macOS\n\nGleicher Ablauf wie unter Windows.\n\n> **Hinweis:** Bei Problemen IT-Support kontaktieren.',
                           author_id=agent.id)
            for doc in [doc1, doc2, doc3]:
                db.session.add(doc)
                db.session.flush()
                rev = DocRevision(page_id=doc.id, content=doc.content, title=doc.title,
                                  author_id=doc.author_id, comment='Initiale Version')
                db.session.add(rev)

            db.session.commit()
            print("Demo-Daten wurden erstellt.")
            print("Login: admin / admin123")


if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
