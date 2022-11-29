import datetime
import json
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render
import plaid
from datetime import datetime, timedelta
from API.models import Account, PlaidItem, Transaction, Category
from .credentials import plaid_secret, plaid_id, plaid_env
from plaid import Client
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.core.paginator import Paginator
# Create your views here.

client = Client(client_id=plaid_id,
				secret=plaid_secret,
				environment=plaid_env,)
logged_in = False

def signup(request):
	return render(request, 'template/signup.html',)

def index(request):
	
	existing_acct = username = latest_transactions = accounts = None
	user = request.user
	global logged_in

	if user.is_authenticated:
		latest_transactions = user.transaction_set.order_by('-date')[:5]
		logged_in = user.is_authenticated
		accounts = user.account_set.order_by('name')
		username = user.username

	data = {
		'latest_transactions': latest_transactions,
		'logged_in': logged_in,
		'accounts': accounts,
		'username': username
	}
	return render(request, 'template/index.html', data)

def create_user(request):
	username = request.POST['username']
	email = request.POST['email']
	password = request.POST['password']
	user = User.objects.create_user(username, email, password)

	user.save()
	print('Account Created Successfully')
	return HttpResponseRedirect('/log_in_form')

def log_in_form(request):
	return render(request, 'template/index.html',)

def log_in(request):
	username = request.POST.get('username')
	password = request.POST.get('password')
	user = authenticate(request, username=username, password=password)
	print(user)
	if user is not None:
		login(request, user)
	else:
		print('invalid credentials')
	return HttpResponseRedirect('/', {'username': username})

def log_out(request):
	logout(request)
	logged_in = False
	return HttpResponseRedirect('/log_in_form')

@csrf_protect
def link_account(request):
	context = {}
	return render(request, 'template/link-account.html', context)

@ensure_csrf_cookie
def create_link_token(request):
	user = request.user

	if user.is_authenticated:
		data = {
			'user': {
				'client_user_id': str(user.id)
			},
			'products': ["transactions"],
			'client_name': "John's Finance App",
			'country_codes': ['US'],
			'language': 'en'
		}

		response = { 'link_token': client.post('link/token/create', data) }

		link_token = response['link_token']
		return JsonResponse(link_token)
	else:
		return HttpResponseRedirect('/')

access_token = None
item_id = None

def get_access_token(request):
	global access_token
	user = request.user

	if user.is_authenticated:
		body_data = json.loads(request.body.decode())
		public_token = body_data["public_token"]
		accounts = body_data["accounts"]

		exchange_response = \
			client.Item.public_token.exchange(public_token)
		access_token = exchange_response['access_token']
		item_id = exchange_response['item_id']

		user = request.user
		plaid_item = None

		try:
			plaid_item = user.plaiditem_set.get(item_id=item_id)
		except:
			new_plaid_item = PlaidItem(user=user, access_token=access_token, item_id=item_id)
			new_plaid_item.save()
			plaid_item = user.plaiditem_set.get(item_id=item_id)

		for account in accounts:
			try:
				existing_acct = user.account_set.get(plaid_account_id=account['account_id'])
				continue
			except:
				new_acct = Account()
				new_acct.plaid_account_id = account['id']
				new_acct.mask = account['mask']
				new_acct.name = account['name']
				new_acct.subtype = account['subtype']
				new_acct.account_type = account['type']
				new_acct.user = user
				new_acct.item = plaid_item
				new_acct.save()

		# Pretty printing in development
		json.dumps(exchange_response, sort_keys=True, indent=4)
		print(exchange_response)

	return redirect('/')

def get_auth(request):
	user = request.user

	try:
		auth_response = client.post('auth/get', access_token)
	except plaid.errors.PlaidError as e:
		return JsonResponse({'error': {'display_message': e.display_message, 'error_code': e.code, 'error_type': e.type } })
	json.dumps(auth_response, sort_keys=True, indent=4)
	print(auth_response)
	return JsonResponse({'error': None, 'auth': auth_response})

def get_transactions(request):
	user = request.user

	if user.is_authenticated:
		transactions = []
		plaid_items = user.plaiditem_set.all()

		timespan_weeks = 4 * 24
		start_date = '{:%Y-%m-%d}'.format(datetime.now() + timedelta(weeks=(-timespan_weeks)))
		end_date = '{:%Y-%m-%d}'.format(datetime.now())

		for item in plaid_items:
			try:
				access_token = item.access_token

				response = client.Transactions.get(access_token,
									start_date=start_date,
									end_date=end_date)

				transactions = response['transactions']
					
				accounts = response['accounts']
				error = None

				for account in accounts:
					try:
						existing_acct = user.account_set.get(plaid_account_id=account['account_id'])
						continue
					except:
						new_acct = Account()
						new_acct.plaid_account_id = account['account_id']
						new_acct.balances = account['balances']
						new_acct.mask = account['mask']
						new_acct.name = account['name']
						new_acct.official_name = account['official_name']
						new_acct.subtype = account['subtype']
						new_acct.account_type = account['type']
						new_acct.user = user
						new_acct.save()

				while len(transactions) < response['total_transactions']:
					response = client.Transactions.get(access_token,
											start_date=start_date,
											end_date=end_date,
											offset=len(transactions)
											)
					transactions.extend(response['transactions'])
			

				for transaction in transactions:
					try:
						existing_trans = user.transaction_set.get(transaction_id=transaction['transaction_id'])
						builtin_cat = Category.objects.get(pk=transaction['builtin_cat_id'])
						existing_trans.builtin_category = builtin_cat
						existing_trans.save()
						continue
					except Transaction.DoesNotExist:
						new_trans = Transaction()
						new_trans.account = user.account_set.get(plaid_account_id=transaction['account_id'])
						new_trans.account_owner = transaction['account_owner']
						new_trans.amount = transaction['amount']
						new_trans.authorized_date = transaction['authorized_date']

						builtin_cat = Category.objects.get(pk=transaction['builtin_cat_id'])
						new_trans.builtin_category = builtin_cat

						new_trans.category = transaction['category']
						new_trans.category_id = transaction['category_id']
						new_trans.date = datetime.strptime(transaction['date'], '%Y-%m-%d')
						new_trans.iso_currency_code = transaction['iso_currency_code']
						new_trans.location = transaction['location']
						new_trans.merchant_name = transaction['merchant_name']
						new_trans.name = transaction['name']
						new_trans.payment_meta = transaction['payment_meta']
						new_trans.payment_channel = transaction['payment_channel']
						new_trans.pending = transaction['pending']
						new_trans.pending_transaction_id = transaction['pending_transaction_id']
						new_trans.transaction_code = transaction['transaction_code']
						new_trans.transaction_id = transaction['transaction_id']
						new_trans.transaction_type = transaction['transaction_type']
						new_trans.unofficial_currency_code = transaction['unofficial_currency_code']
						new_trans.user = user
						new_trans.save()
			except Exception as e:
				print(e)
				# error = {'display_message': 'You need to link your account.' }
		json.dumps(transactions, sort_keys=True, indent=4)
		print(transactions)
		return HttpResponseRedirect('/',{'error': error, 'transactions': transactions})
	else:
		redirect('/')


def refresh_accounts(request):
	user = request.user
	items = user.plaiditem_set.all()

	for item in items:
		access_token = item.access_token
		response = client.Accounts.get(access_token)

		accounts = response['accounts']
		for account in accounts:
			acc = Account.objects.get(plaid_account_id=account['account_id'])
			acc.balances = account['balances']
			acc.mask = account['mask']
			acc.name = account['name']
			acc.official_name = account['official_name']
			acc.subtype = account['subtype']
			acc.account_type = account['type']
			acc.save()


	return HttpResponseRedirect('/')

def transactions(request):

	username = all_transactions = None
	user = request.user

	if user.is_authenticated:
		all_transactions = user.transaction_set.order_by('-date')
		paginator = Paginator(all_transactions, 100)

		page_number = request.GET.get('page')
		page_obj = paginator.get_page(page_number)

		logged_in = user.is_authenticated
		username = user.username

		builtin_categories = Category.objects.filter(custom=False).order_by('description')

	data = {
		'all_transactions': all_transactions,
		'logged_in': logged_in,
		'username': username,
		'page_obj': page_obj,
		'builtin_categories': builtin_categories
	}
	return render(request, 'template/transactions.html', data)
