from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from .forms import ConversionForm, CustomSignupForm, CustomLoginForm
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import ConversionForm
from .models import Conversion
try:
    from .utils import preprocess_pidgin
except ImportError:
    def preprocess_pidgin(text):
        messages.error(None, "Pidgin preprocessing unavailable. Using raw text.")
        return text
import PyPDF2
import threading
from gtts import gTTS
from django.core.files import File
from django.core.files.base import ContentFile
import os
from django.conf import settings
from django.http import JsonResponse
import hashlib

gtts_lock = threading.Lock()

def home(request):
    return render(request, 'core/home.html')

def signup(request):
    if request.method == 'POST':
        form = CustomSignupForm(request.POST)
        if form.is_valid():
            form.save()
            username = form.cleaned_data.get('username')
            messages.success(request, f'Account created for {username}!')
            return redirect('login')
        else:
            messages.error(request, 'Error creating account. Please check the form.')
    else:
        form = CustomSignupForm()
    return render(request, 'core/signup.html', {'form': form})


def login_view(request):
    if request.method == 'POST':
        form = CustomLoginForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)
                messages.success(request, f'Welcome, {username}!')
                return redirect('home')
            else:
                messages.error(request, 'Invalid username or password.')
        else:
            messages.error(request, 'Invalid username or password.')
    else:
        form = CustomLoginForm()
    return render(request, 'core/login.html', {'form': form})


def logout_view(request):
    logout(request)
    messages.success(request, 'You have been logged out.')
    return redirect('home')

@login_required
def convert_text(request):
    if request.method == 'POST':
        form = ConversionForm(request.POST, request.FILES)
        if form.is_valid():
            conversion = form.save(commit=False)
            conversion.user = request.user
            conversion.save()

            threading.Thread(target=process_conversion, args=(conversion,)).start()
            
            return redirect('conversion_result', conversion_id=conversion.id)
        else:
            messages.error(request, 'Invalid input. Please check the form: ' + str(form.errors))
    else:
        form = ConversionForm()
    return render(request, 'core/convert_text.html', {'form': form})

def process_conversion(conversion_obj):
    try:
        if conversion_obj.pdf_file:
            pdf_reader = PyPDF2.PdfReader(conversion_obj.pdf_file)
            text = ''
            for page in pdf_reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + ' '
            conversion_obj.input_text = text.strip()
        
        if not conversion_obj.input_text:
            conversion_obj.input_text = "No text provided."
        
        text_to_convert = preprocess_pidgin(conversion_obj.input_text) if conversion_obj.language == 'pidgin' else conversion_obj.input_text
        
        unique_id = hashlib.md5((text_to_convert + conversion_obj.language).encode('utf-8')).hexdigest()
        audio_filename = f'{unique_id}.mp3'
        audio_path = os.path.join(settings.MEDIA_ROOT, 'audio', audio_filename)

        if not os.path.exists(audio_path):
            print(f"No cached file found. Generating new audio for: {unique_id}")

            with gtts_lock:
                if not os.path.exists(audio_path):
                    try:
                        tts = gTTS(text=text_to_convert, lang='en')
                        os.makedirs(os.path.dirname(audio_path), exist_ok=True)
                        tts.save(audio_path)
                    except Exception as e:
                        print(f"gTTS API call failed: {e}")
                        raise e
        else:
            print(f"Cached file found. Serving audio from cache for: {unique_id}")

        with open(audio_path, 'rb') as f:
            conversion_obj.audio_file.save(audio_filename, ContentFile(f.read()))

        conversion_obj.save()
        
    except Exception as e:
        print(f"Error processing conversion: {e}")
        conversion_obj.input_text = f"Error: {str(e)}"
        conversion_obj.save()

@login_required
def conversion_result(request, conversion_id):
    try:
        conversion = Conversion.objects.get(id=conversion_id, user=request.user)
        return render(request, 'core/result.html', {'conversion': conversion})
    except Conversion.DoesNotExist:
        messages.error(request, 'Conversion not found or you do not have access.')
        return redirect('home')

@login_required
def check_audio_status(request, conversion_id):
    try:
        conversion = Conversion.objects.get(id=conversion_id, user=request.user)
        if conversion.input_text and conversion.input_text.startswith('Error:'):
            return JsonResponse({'audio_ready': False, 'error': conversion.input_text})
        if conversion.audio_file and conversion.audio_file.name:
            return JsonResponse({'audio_ready': True, 'audio_url': conversion.audio_file.url})
        return JsonResponse({'audio_ready': False})
    except Conversion.DoesNotExist:
        return JsonResponse({'audio_ready': False, 'error': 'Conversion not found'}, status=404)